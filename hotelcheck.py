from __future__ import print_function

from argparse import Action, ArgumentParser, ArgumentError, ArgumentTypeError, SUPPRESS
from datetime import datetime, timedelta
from json import loads as fromJS, dumps as toJS
from re import compile as reCompile, IGNORECASE as RE_IGNORECASE
from sys import stdout, version_info
from threading import Thread

from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, urlopen, build_opener

firstDay, lastDay, startDay = datetime(2020, 7, 25), datetime(2020, 8, 4), datetime(2020, 7, 30)
eventId = 50023680
ownerId = 10909638

distanceUnits = {
    1: 'blocks',
    2: 'yards',
    3: 'miles',
    4: 'meters',
    5: 'kilometers',
}


class PasskeyParser(HTMLParser):
    def __init__(self, resp):
        HTMLParser.__init__(self)
        self.json = None
        self.feed(resp.read().decode('utf8'))
        self.close()

    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'script':
            attrs = dict(attrs)
            if attrs.get('id', '').lower() == 'last-search-results':
                self.json = True

    def handle_data(self, data):
        if self.json is True:
            self.json = data


try:
    from html import unescape

    PasskeyParser.unescape = lambda self, text: unescape(text)
except ImportError as e:
    pass


def type_day(arg):
    try:
        d = datetime.strptime(arg, '%Y-%m-%d')
    except ValueError:
        raise ArgumentTypeError("%s is not a date in the form YYYY-MM-DD" % arg)
    if not firstDay <= d <= lastDay:
        raise ArgumentTypeError("%s is outside the Gencon housing block window" % arg)
    return arg


def type_distance(arg):
    if arg == 'connected':
        return arg
    try:
        return float(arg)
    except ValueError:
        raise ArgumentTypeError("invalid float value: '%s'" % arg)


def type_regex(arg):
    try:
        return reCompile(arg, RE_IGNORECASE)
    except Exception as e:
        raise ArgumentTypeError("invalid regex '%s': %s" % (arg, e))


class PasskeyUrlAction(Action):
    def __call__(self, parser, namespace, values, option_string=None):
        m = reCompile('^https://book.passkey.com/reg/([0-9A-Z]{8}-[0-9A-Z]{4})/([0-9a-f]{1,64})$').match(values)
        if m:
            setattr(namespace, self.dest, m.groups())
        else:
            raise ArgumentError(self, "invalid passkey url: '%s'" % values)


class SurnameAction(Action):
    def __call__(self, parser, namespace, values, option_string=None):
        raise ArgumentError(self, "option no longer exists. Surname should be passed along with the key")


class EmailAction(Action):
    def __call__(self, parser, namespace, values, option_string=None):
        dest = getattr(namespace, self.dest)
        if dest is None:
            dest = []
            setattr(namespace, self.dest, dest)
        dest.append(tuple(['email'] + values))


parser = ArgumentParser()
parser.add_argument('--surname', '--lastname', action=SurnameAction, help=SUPPRESS)
parser.add_argument('--guests', type=int, default=1, help='number of guests')
parser.add_argument('--children', type=int, default=0, help='number of children')
parser.add_argument('--rooms', type=int, default=1, help='number of rooms')
group = parser.add_mutually_exclusive_group()
group.add_argument('--checkin', type=type_day, metavar='YYYY-MM-DD', default=startDay.strftime('%Y-%m-%d'), help='check in')
group.add_argument('--wednesday', dest='checkin', action='store_const', const=(startDay - timedelta(1)).strftime('%Y-%m-%d'), help='check in on Wednesday')
parser.add_argument('--checkout', type=type_day, metavar='YYYY-MM-DD', default=(startDay + timedelta(3)).strftime('%Y-%m-%d'), help='check out')
group = parser.add_mutually_exclusive_group()
group.add_argument('--max-distance', type=type_distance, metavar='BLOCKS', help="max hotel distance that triggers an alert (or 'connected' to require skywalk hotels)")
group.add_argument('--connected', dest='max_distance', action='store_const', const='connected', help='shorthand for --max-distance connected')
parser.add_argument('--budget', type=float, metavar='PRICE', default='99999', help='max total rate (not counting taxes/fees) that triggers an alert')
parser.add_argument('--hotel-regex', type=type_regex, metavar='PATTERN', default=reCompile('.*'), help='regular expression to match hotel name against')
parser.add_argument('--room-regex', type=type_regex, metavar='PATTERN', default=reCompile('.*'), help='regular expression to match room against')
parser.add_argument('--show-all', action='store_true', help='show all rooms, even if miles away (these rooms never trigger alerts)')
group = parser.add_mutually_exclusive_group()
group.add_argument('--delay', type=int, default=1, metavar='MINS', help='search every MINS minute(s)')
group.add_argument('--once', action='store_true', help='search once and exit')
parser.add_argument('--test', action='store_true', dest='test', help='trigger every specified alert and exit')

group = parser.add_argument_group('required arguments')
# Both of these set 'key'; only one of them is required
group.add_argument('--key', nargs=2, metavar=('KEY', 'AUTH'), help='key (see the README for more information)')
group.add_argument('--url', action=PasskeyUrlAction, dest='key', help='passkey URL containing your key')

group = parser.add_argument_group('alerts')
group.add_argument('--popup', dest='alerts', action='append_const', const=('popup',), help='show a dialog box')
group.add_argument('--cmd', dest='alerts', action='append', type=lambda arg: ('cmd', arg), metavar='CMD', help='run the specified command, passing each hotel name as an argument')
group.add_argument('--browser', dest='alerts', action='append_const', const=('browser',), help='open the Passkey website in the default browser')
group.add_argument('--email', dest='alerts', action=EmailAction, nargs=3, metavar=('HOST', 'FROM', 'TO'), help='send an e-mail')

args = parser.parse_args()

baseUrl = "https://book.passkey.com/event/%d/owner/%d" % (eventId, ownerId)
lastAlerts = set()
opener = build_opener(HTTPCookieProcessor())


def send(name, *args):
    try:
        resp = opener.open(*args)
        if resp.getcode() != 200:
            raise RuntimeError("%s failed: %d" % (name, resp.getcode()))
        return resp
    except URLError as e:
        raise RuntimeError("%s failed: %s" % (name, e))


class ConHotel:
    def __init__(self, args):
        self.args = args

    def searchNew(self):
        '''Search using a reservation key (for users who don't have a booking yet)'''
        resp = send('Session request', "https://book.passkey.com/reg/%s/%s" % tuple(self.args.key))
        data = {
            'blockMap.blocks[0].blockId': '0',
            'blockMap.blocks[0].checkIn': self.args.checkin,
            'blockMap.blocks[0].checkOut': self.args.checkout,
            'blockMap.blocks[0].numberOfGuests': str(self.args.guests),
            'blockMap.blocks[0].numberOfRooms': str(self.args.rooms),
            'blockMap.blocks[0].numberOfChildren': str(self.args.children),
        }
        return send('Search', baseUrl + '/rooms/select', urlencode(data).encode('utf8'))

    def searchExisting(self, hash=[]):
        '''Search using an acknowledgement number (for users who have booked a room)'''
        # The hash doesn't change, so it's only calculated the first time
        if not hash:
            send('Session request', baseUrl + '/home')
            data = {
                'ackNum': self.args.key[0],
                'lastName': self.args.key[1],
            }
            resp = send('Finding reservation', Request(baseUrl + '/reservation/find', toJS(data).encode('utf8'), {'Content-Type': 'application/json'}))
            try:
                respData = fromJS(resp.read())
            except Exception as e:
                raise RuntimeError("Failed to decode reservation: %s" % e)
            if respData.get('ackNum', None) != self.args.key[0]:
                raise RuntimeError("Reservation not found. Are your acknowledgement number and surname correct?")
            if 'hash' not in respData:
                raise RuntimeError("Hash missing from reservation data")
            hash.append(respData['hash'])

        data = {
            'blockMap': {
                'blocks': [{
                    'blockId': '0',
                    'checkIn': self.args.checkin,
                    'checkOut': self.args.checkout,
                    'numberOfGuests': str(self.args.guests),
                    'numberOfRooms': str(self.args.rooms),
                    'numberOfChildren': str(self.args.children),
                }]
            },
        }
        send('Loading existing reservation', baseUrl + "/r/%s/%s" % (self.args.key[0], hash[0]))
        send('Search', Request(baseUrl + '/rooms/select/search', toJS(data).encode('utf8'), headers={'Content-Type': 'application/json'}))

    def parseResults(self):
        resp = send('List', baseUrl + '/list/hotels')
        parser = PasskeyParser(resp)
        if not parser.json:
            raise RuntimeError("Failed to find search results")

        hotels = fromJS(parser.json)

        print("Results:   (%s)" % datetime.now())
        alerts = []

        results = ""
        for hotel in hotels:
            for block in hotel['blocks']:
                # Don't show hotels miles away unless requested
                if hotel['distanceUnit'] == 3 and not self.args.show_all:
                    continue

                connected = ('Skywalk to ICC' in (hotel['messageMap'] or ''))
                simpleHotel = {
                    'name': parser.unescape(hotel['name']),
                    'distance': 'Skywalk' if connected else "%4.1f %s" % (hotel['distanceFromEvent'], distanceUnits.get(hotel['distanceUnit'], '???')),
                    'price': int(sum(inv['rate'] for inv in block['inventory'])),
                    # 'rooms': min(inv['available'] for inv in block['inventory']),
                    'room': parser.unescape(block['name']),
                }
                if min(inv['available'] for inv in block['inventory']) == 0:
                    continue
                result = "%-15s $%-9s %-80s %s" % (simpleHotel['distance'], simpleHotel['price'], simpleHotel['name'], simpleHotel['room'])
                # I don't think these distances (yards, meters, kilometers) actually appear in the results, but if they do assume it must be close enough regardless of --max-distance
                closeEnough = hotel['distanceUnit'] in (2, 4, 5) or \
                              (hotel['distanceUnit'] == 1 and (self.args.max_distance is None or (isinstance(self.args.max_distance, float) and hotel['distanceFromEvent'] <= self.args.max_distance))) or \
                              (self.args.max_distance == 'connected' and connected) or self.args.show_all
                cheapEnough = simpleHotel['price'] <= self.args.budget
                regexMatch = self.args.hotel_regex.search(simpleHotel['name']) and self.args.room_regex.search(simpleHotel['room'])
                if closeEnough and cheapEnough and regexMatch:
                    alerts.append(simpleHotel)

                results += "%s\r\n" % result
        if results:
            print("   %-15s %-10s %-80s %s" % ('Distance', 'Price', 'Hotel', 'Room'))
            print(results)

        global lastAlerts
        if alerts:
            alertHash = {(alert['name'], alert['room']) for alert in alerts}
            if alertHash == lastAlerts:
                print("Skipped alerts (no changes in nearby hotel list)")
            else:
                numHotels = len(set(alert['name'] for alert in alerts))
                preamble = "%d %s near the ICC:" % (numHotels, 'hotel' if numHotels == 1 else 'hotels')
                print("Triggered alerts")
                lastAlerts = alertHash
                return preamble, alerts
        else:
            alertHash = set()

        lastAlerts = alertHash
        return None, None
