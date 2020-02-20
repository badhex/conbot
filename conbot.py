import os

import asyncio
import discord
from dotenv import load_dotenv
from discord.ext import tasks, commands
from argparse import Namespace
import re
import traceback
from random import randint

from hotelcheck import ConHotel

load_dotenv()
token = os.getenv('DISCORD_TOKEN')
GUILD = os.getenv('DISCORD_GUILD')
CHANNEL = os.getenv('DISCORD_CHANNEL')
KEY1 = os.getenv('HOUSING_KEY1')
KEY2 = os.getenv('HOUSING_KEY2')
args = Namespace(alerts=None,
                 budget=99999.0,
                 checkin='2020-07-31',
                 checkout='2020-08-02',
                 children=0,
                 guests=2,
                 hotel_regex=re.compile('.*'),
                 key=(KEY1, KEY2),
                 max_distance=None,
                 once=False,
                 room_regex=re.compile('.*'),
                 rooms=1,
                 show_all=True,
                 surname=None)

client = discord.Client()


@client.event
async def on_ready():
    for guild in client.guilds:
        if guild.name == GUILD:
            break

    ch = client.get_channel(int(CHANNEL))

    print(
        f'{client.user} is connected to the following guild:\n'
        f'{guild.name}(id: {guild.id})'
    )

    await ch.send(
        "I'm currently watching for new gencon hotels near the ICC\r\nSearching... (%d %s, %d %s, %s - %s, %s)" % (args.guests, 'guest' if args.guests == 1 else 'guests', args.rooms, 'room' if args.rooms == 1 else 'rooms', args.checkin, args.checkout,
                                                                                                                   'connected' if args.max_distance == 'connected' else 'downtown' if args.max_distance is None else "within %.1f blocks" % args.max_distance))
    activity = discord.Activity(name='Gen Con Hotels', type=discord.ActivityType.watching)
    await client.change_presence(activity=activity)


def diff(a, b):
    a = [] if a is None else a
    b = [] if b is None else b
    return [item for item in a if item not in b]


class MyCog(commands.Cog):
    def __init__(self, bot):
        self.index = 0
        self.bot = bot
        self.channel = None
        self.search = ConHotel(args)
        self.lasthotels = None
        self.printer.start()
        self.alertlist = []

    def cog_unload(self):
        self.printer.cancel()

    @tasks.loop(seconds=10.0)
    async def printer(self):
        self.index += 1
        if self.index == 1:
            await asyncio.sleep(3)
        else:
            await asyncio.sleep(randint(10, 50))
        print("Search round: %s" % self.index)
        try:
            self.search.searchNew()
            preamble, hotels = self.search.parseResults()
            # first list the preamble and the additions
            if preamble is not None:
                text = "```diff\r\n"
                text += "%s\r\n  %-15s %-10s %-80s %s\r\n" % (preamble, 'Distance', 'Price', 'Hotel', 'Room')
                # check to see what was removed
                removed = diff(self.lasthotels, hotels)
                for hotel in removed:
                    if len("%s%s" % (text, "-%-15s $%-10s %-80s %s\r\n" % (hotel['distance'], hotel['price'], hotel['name'], hotel['room']))) >= 1996:
                        await self.channel.send("%s```" % text)
                        text = "```diff\r\n"
                    text += "-%-15s $%-10s %-80s %s\r\n" % (hotel['distance'], hotel['price'], hotel['name'], hotel['room'])
                # check what was added
                added = diff(hotels, self.lasthotels)
                for hotel in added:
                    if len("%s%s" % (text, "+%-15s $%-10s %-80s %s\r\n" % (hotel['distance'], hotel['price'], hotel['name'], hotel['room']))) >= 1996:
                        await self.channel.send("%s```" % text)
                        text = "```diff\r\n"
                    text += "+%-15s $%-10s %-80s %s\r\n" % (hotel['distance'], hotel['price'], hotel['name'], hotel['room'])

                if text != "```diff\r\n":
                    await self.channel.send("%s```" % text)

                self.lasthotels = hotels
        except Exception as e:
            print(traceback.format_exc())

    @printer.before_loop
    async def before_printer(self):
        print('waiting to be ready...')
        await self.bot.wait_until_ready()
        self.channel = self.bot.get_channel(int(CHANNEL))


cog = MyCog(client)
client.run(token)
