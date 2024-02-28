import discord
from discord.ext import tasks, commands
import subprocess
from datetime import datetime, timedelta
import asyncio
from aiofiles import open as aio_open
from aiofiles.os import stat as aio_stat
import os
from dotenv import load_dotenv
import json

# Load environment variables
load_dotenv()

# Accessing variables from .env file
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
SCRIPTS_CONFIG = os.getenv('SCRIPTS_CONFIG')

# Load scripts configuration from a JSON file
with open(SCRIPTS_CONFIG, 'r') as f:
    scripts = json.load(f)

bot = commands.Bot(command_prefix='!cb', intents=discord.Intents.default())


async def get_file_size(file_path):
    return (await aio_stat(file_path)).st_size


async def tail(file_path, offset):
    async with aio_open(file_path, 'r') as file:
        await file.seek(offset)
        return await file.read()


async def run_script(script):
    # Launch the process
    process = subprocess.Popen([script['path']], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    # Wait for the process to start and allocate time for execution
    await asyncio.sleep(1)  # Adjust based on expected execution time
    process.terminate()

    # Check for changes in the output file
    new_size = await get_file_size(script['output_path'])
    if new_size > script.get('last_size', 0):
        changes = await tail(script['output_path'], script.get('last_size', 0))

        # Iterate over all guilds the bot is a member of
        for guild in bot.guilds:
            # Iterate over all text channels in the guild
            for channel in guild.text_channels:
                try:
                    await channel.send(f"```{changes}```")
                except discord.Forbidden:
                    print(f"Missing permissions to send messages in {channel.name} of {guild.name}")
                except discord.HTTPException as e:
                    print(f"Failed to send message in {channel.name} of {guild.name}: {e}")

        script['last_size'] = new_size


@tasks.loop(seconds=1)
async def scheduler():
    for script in scripts:
        if datetime.now() >= script['next_run']:
            await run_script(script)
            script['next_run'] = datetime.now() + timedelta(seconds=script['interval'])


@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    for script in scripts:
        script['last_size'] = await get_file_size(script['output_path'])
        script['next_run'] = datetime.now()  # Initialize next run time
    scheduler.start()  # Start the scheduler task loop


@bot.command()
async def start(ctx):
    """Starts the scheduler task."""
    if not scheduler.is_running():
        scheduler.start()


@bot.command()
async def stop(ctx):
    """Stops the scheduler task."""
    scheduler.stop()


bot.run(DISCORD_TOKEN)
