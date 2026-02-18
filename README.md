# Discord-Bot-Console
Interactive Discord bot access-analysis console.

![1](https://i.imgur.com/cGn4Z2B.png)
![2](https://i.imgur.com/4edDbQ8.png)

Designed to understand:
- what a bot token can access,
- what permissions the bot has,
- what server(s) it is in (owner, members, channels, messages).

Use cases:
- forensic review,
- malware/bot triage,
- validating and testing your own bot setup.

## Setup

```bash
pip install -U discord.py rich
```

## Run

```bash
python discord_bot_token.py
```

Optional (PowerShell):

```powershell
$env:DISCORD_BOT_TOKEN="your_bot_token"
python discord_bot_token.py
```

## Features

- Token triage before connect
- Guild/channel permission checks
- Guild ownership/member/context visibility
- Read last 25/100 messages
- Send message
- Create invite (if permitted)
- LIVE channel watch
- Export triage JSON (`triage_<guild_id>_<timestamp>.json`)
