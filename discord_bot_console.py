import asyncio
import base64
import json
from datetime import datetime, timezone
from typing import Any

import discord
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table
from rich.text import Text

console = Console()

watch_channel_id: int | None = None
watch_enabled = False


def fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "?"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def clip(text: str | None, size: int = 220) -> str:
    if not text:
        return ""
    compact = text.replace("\n", "\\n")
    return compact if len(compact) <= size else compact[:size] + "..."


def enum_to_primitive(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "value"):
        return value.value
    return value


def decode_token_meta(token: str) -> dict[str, Any]:
    info: dict[str, Any] = {
        "raw_length": len(token),
        "parts": token.count(".") + 1,
        "user_id": None,
        "created_utc": None,
        "looks_like_bot_token": False,
        "error": None,
    }

    parts = token.split(".")
    if len(parts) != 3:
        info["error"] = "Token does not have 3 dot-separated sections."
        return info

    try:
        pad = "=" * (-len(parts[0]) % 4)
        decoded = base64.urlsafe_b64decode(parts[0] + pad).decode("utf-8", errors="ignore")
        user_id = int(decoded)
        info["user_id"] = user_id
        discord_epoch = 1420070400000
        created_ms = (user_id >> 22) + discord_epoch
        info["created_utc"] = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc)
        info["looks_like_bot_token"] = True
    except Exception as exc:
        info["error"] = f"Unable to decode token metadata: {exc}"

    return info


def build_client() -> discord.Client:
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    return discord.Client(intents=intents)


async def ainput(prompt: str) -> str:
    return await asyncio.to_thread(Prompt.ask, prompt)


async def secure_token_prompt() -> str:
    return await asyncio.to_thread(Prompt.ask, "Enter bot token", password=True)


def render_token_triage(token: str) -> None:
    meta = decode_token_meta(token)
    table = Table(title="Token Triage", box=box.SIMPLE_HEAVY)
    table.add_column("Field", style="cyan", no_wrap=True)
    table.add_column("Value", style="white")
    table.add_row("Length", str(meta["raw_length"]))
    table.add_row("Sections", str(meta["parts"]))
    table.add_row("Looks Bot Token", str(meta["looks_like_bot_token"]))
    table.add_row("Decoded User ID", str(meta["user_id"] or "?"))
    table.add_row("Decoded Created", fmt_dt(meta["created_utc"]))
    if meta["error"]:
        table.add_row("Decode Note", str(meta["error"]))
    console.print(table)


def guild_summary(guild: discord.Guild, me: discord.Member | None) -> dict[str, int]:
    totals = {
        "text_total": len(guild.text_channels),
        "text_viewable": 0,
        "text_history": 0,
        "text_send": 0,
        "text_invite": 0,
        "voice_total": len(guild.voice_channels),
    }
    if me is None:
        return totals

    for channel in guild.text_channels:
        perms = channel.permissions_for(me)
        if perms.view_channel:
            totals["text_viewable"] += 1
        if perms.read_message_history:
            totals["text_history"] += 1
        if perms.send_messages:
            totals["text_send"] += 1
        if perms.create_instant_invite:
            totals["text_invite"] += 1
    return totals


def print_session_overview(client: discord.Client) -> None:
    total_members = sum(g.member_count or 0 for g in client.guilds)
    panel_text = (
        f"[bold]User:[/bold] {client.user} ({client.user.id})\n"
        f"[bold]Guilds:[/bold] {len(client.guilds)}\n"
        f"[bold]Members (cached):[/bold] {total_members}\n"
        f"[bold]Latency:[/bold] {round(client.latency * 1000, 1)} ms"
    )
    console.print(Panel(panel_text, title="Discord Bot Console", border_style="green"))


async def choose_guild(client: discord.Client) -> discord.Guild | None:
    if not client.guilds:
        console.print("[red]No guilds available for this bot.[/red]")
        return None

    table = Table(title="Guilds", box=box.MINIMAL_DOUBLE_HEAD)
    table.add_column("#", justify="right", style="cyan")
    table.add_column("Guild", style="bold white")
    table.add_column("ID", style="dim")
    table.add_column("Members", justify="right")

    for i, guild in enumerate(client.guilds, start=1):
        table.add_row(str(i), guild.name, str(guild.id), str(guild.member_count or "?"))
    console.print(table)

    raw = (await ainput("Pick guild number ([dim]blank to exit[/dim])")).strip()
    if raw == "":
        return None
    if not raw.isdigit():
        console.print("[yellow]Invalid selection.[/yellow]")
        return await choose_guild(client)
    idx = int(raw)
    if idx < 1 or idx > len(client.guilds):
        console.print("[yellow]Out of range.[/yellow]")
        return await choose_guild(client)
    return client.guilds[idx - 1]


async def choose_channel(guild: discord.Guild) -> discord.TextChannel | None:
    me = guild.me
    rows: list[discord.TextChannel] = []
    table = Table(title=f"Text Channels: {guild.name}", box=box.SIMPLE)
    table.add_column("#", justify="right", style="cyan")
    table.add_column("Channel")
    table.add_column("View")
    table.add_column("History")
    table.add_column("Send")
    table.add_column("Invite")

    for channel in guild.text_channels:
        perms = channel.permissions_for(me)
        if perms.view_channel:
            rows.append(channel)
            table.add_row(
                str(len(rows)),
                f"#{channel.name}",
                "Y" if perms.view_channel else "N",
                "Y" if perms.read_message_history else "N",
                "Y" if perms.send_messages else "N",
                "Y" if perms.create_instant_invite else "N",
            )

    if not rows:
        console.print("[yellow]No viewable text channels.[/yellow]")
        return None

    console.print(table)
    raw = (await ainput("Pick channel number ([dim]blank to cancel[/dim])")).strip()
    if raw == "":
        return None
    if not raw.isdigit():
        console.print("[yellow]Invalid selection.[/yellow]")
        return await choose_channel(guild)
    idx = int(raw)
    if idx < 1 or idx > len(rows):
        console.print("[yellow]Out of range.[/yellow]")
        return await choose_channel(guild)
    return rows[idx - 1]


def show_channel_perms(channel: discord.TextChannel) -> discord.Permissions:
    perms = channel.permissions_for(channel.guild.me)
    table = Table(title=f"Permissions: #{channel.name}", box=box.ROUNDED)
    table.add_column("Permission", style="cyan")
    table.add_column("State", justify="center")
    table.add_row("View Channel", "Y" if perms.view_channel else "N")
    table.add_row("Read History", "Y" if perms.read_message_history else "N")
    table.add_row("Send Messages", "Y" if perms.send_messages else "N")
    table.add_row("Create Instant Invite", "Y" if perms.create_instant_invite else "N")
    table.add_row("Manage Messages", "Y" if perms.manage_messages else "N")
    table.add_row("Embed Links", "Y" if perms.embed_links else "N")
    table.add_row("Attach Files", "Y" if perms.attach_files else "N")
    table.add_row("Bitfield", str(perms.value))
    console.print(table)
    return perms


async def fetch_messages(channel: discord.TextChannel, limit: int) -> None:
    guild = channel.guild
    me = await guild.fetch_member(channel._state.user.id)
    fresh = await guild.fetch_channel(channel.id)
    perms = fresh.permissions_for(me)

    if not perms.view_channel:
        console.print("[red]Bot cannot view this channel.[/red]")
        return
    if not perms.read_message_history:
        console.print("[red]Bot cannot read message history in this channel.[/red]")
        return

    try:
        messages = [m async for m in fresh.history(limit=limit, oldest_first=True)]
    except discord.Forbidden:
        console.print("[red]Discord returned 403 while fetching history.[/red]")
        return
    except discord.HTTPException as exc:
        console.print(f"[red]HTTP error while fetching history: {exc}[/red]")
        return

    if not messages:
        console.print("[yellow]No messages returned.[/yellow]")
        return

    table = Table(title=f"Last {len(messages)} messages in #{channel.name}", box=box.MINIMAL)
    table.add_column("Time", style="dim")
    table.add_column("Author", style="cyan")
    table.add_column("Content", style="white")
    for message in messages:
        table.add_row(fmt_dt(message.created_at), str(message.author), clip(message.content, 180))
    console.print(table)


async def send_message(channel: discord.TextChannel) -> None:
    text = (await ainput("Message text")).strip()
    if not text:
        console.print("[yellow]Message is empty.[/yellow]")
        return
    try:
        sent = await channel.send(text)
        console.print(f"[green]Sent message id={sent.id}[/green]")
    except discord.Forbidden:
        console.print("[red]Bot cannot send messages here.[/red]")
    except discord.HTTPException as exc:
        console.print(f"[red]HTTP error while sending: {exc}[/red]")


async def create_invite(channel: discord.TextChannel) -> None:
    perms = channel.permissions_for(channel.guild.me)
    if not perms.create_instant_invite:
        console.print("[red]Bot lacks Create Instant Invite in this channel.[/red]")
        return

    max_age = await asyncio.to_thread(IntPrompt.ask, "Invite max age seconds (0 never expires)", default=600)
    max_uses = await asyncio.to_thread(IntPrompt.ask, "Invite max uses (0 unlimited)", default=1)

    try:
        invite = await channel.create_invite(
            max_age=max_age,
            max_uses=max_uses,
            temporary=False,
            unique=True,
            reason="Created via rich triage console",
        )
        console.print(f"[green]Invite created:[/green] {invite.url}")
    except discord.Forbidden:
        console.print("[red]Server/channel settings blocked invite creation.[/red]")
    except discord.HTTPException as exc:
        console.print(f"[red]HTTP error creating invite: {exc}[/red]")


def build_triage_report(guild: discord.Guild) -> dict[str, Any]:
    me = guild.me
    summary = guild_summary(guild, me)
    me_guild_perms = me.guild_permissions if me else discord.Permissions.none()

    owner_display = "?"
    if getattr(guild, "owner", None):
        owner_display = f"{guild.owner} ({guild.owner.id})"
    elif guild.owner_id:
        owner_display = f"owner_id={guild.owner_id}"

    channel_type_counts: dict[str, int] = {
        "text": len(guild.text_channels),
        "voice": len(guild.voice_channels),
        "categories": len(guild.categories),
        "stage": len(guild.stage_channels),
        "forum": len(guild.forums),
    }

    channels: list[dict[str, Any]] = []
    no_view: list[dict[str, Any]] = []
    no_history: list[dict[str, Any]] = []
    no_send: list[dict[str, Any]] = []
    no_invite: list[dict[str, Any]] = []

    for channel in guild.text_channels:
        perms = channel.permissions_for(me)
        row = {
            "name": channel.name,
            "id": channel.id,
            "type": str(channel.type),
            "category": channel.category.name if channel.category else None,
            "position": channel.position,
            "nsfw": channel.is_nsfw(),
            "slowmode_seconds": channel.slowmode_delay,
            "topic": clip(channel.topic, 140),
            "view": perms.view_channel,
            "history": perms.read_message_history,
            "send": perms.send_messages,
            "invite": perms.create_instant_invite,
            "manage_messages": perms.manage_messages,
            "manage_channels": perms.manage_channels,
            "embed_links": perms.embed_links,
            "attach_files": perms.attach_files,
        }
        channels.append(row)

        if not perms.view_channel:
            no_view.append(row)
        if perms.view_channel and not perms.read_message_history:
            no_history.append(row)
        if perms.view_channel and not perms.send_messages:
            no_send.append(row)
        if perms.view_channel and not perms.create_instant_invite:
            no_invite.append(row)

    return {
        "generated_utc": datetime.now(tz=timezone.utc).isoformat(),
        "guild": {
            "name": guild.name,
            "id": guild.id,
            "owner": owner_display,
            "owner_id": guild.owner_id,
            "created_utc": fmt_dt(guild.created_at),
            "member_count": guild.member_count,
            "description": guild.description,
            "verification_level": str(guild.verification_level),
            "mfa_level": enum_to_primitive(guild.mfa_level),
            "nsfw_level": str(guild.nsfw_level),
            "premium_tier": enum_to_primitive(guild.premium_tier),
            "premium_subscribers": guild.premium_subscription_count,
            "afk_timeout": guild.afk_timeout,
            "system_channel_id": guild.system_channel.id if guild.system_channel else None,
            "features": sorted(list(guild.features)),
        },
        "bot": {
            "member_id": me.id if me else None,
            "display_name": str(me) if me else "?",
            "top_role": me.top_role.name if me else "?",
            "guild_admin": me_guild_perms.administrator,
            "manage_guild": me_guild_perms.manage_guild,
            "manage_roles": me_guild_perms.manage_roles,
            "manage_channels": me_guild_perms.manage_channels,
            "kick_members": me_guild_perms.kick_members,
            "ban_members": me_guild_perms.ban_members,
            "view_audit_log": me_guild_perms.view_audit_log,
            "guild_perm_value": me_guild_perms.value,
        },
        "summary": summary,
        "channel_types": channel_type_counts,
        "roles_total": len(guild.roles),
        "emojis_total": len(guild.emojis),
        "stickers_total": len(guild.stickers),
        "risk_flags": {
            "text_no_view_count": len(no_view),
            "view_but_no_history_count": len(no_history),
            "view_but_no_send_count": len(no_send),
            "view_but_no_invite_count": len(no_invite),
            "view_but_no_history": [f"#{c['name']}" for c in no_history],
            "view_but_no_send": [f"#{c['name']}" for c in no_send],
            "view_but_no_invite": [f"#{c['name']}" for c in no_invite],
        },
        "channels": channels,
    }


def show_guild_triage(guild: discord.Guild) -> None:
    report = build_triage_report(guild)
    guild_info = report["guild"]
    bot_info = report["bot"]
    summary = report["summary"]
    channel_types = report["channel_types"]
    risk_flags = report["risk_flags"]

    identity = Table(title=f"Guild Triage: {guild.name}", box=box.HEAVY_HEAD)
    identity.add_column("Field", style="cyan")
    identity.add_column("Value")
    identity.add_row("Guild", f"{guild_info['name']} ({guild_info['id']})")
    identity.add_row("Owner", str(guild_info["owner"]))
    identity.add_row("Created", str(guild_info["created_utc"]))
    identity.add_row("Members", str(guild_info["member_count"] or "?"))
    identity.add_row("Verification", str(guild_info["verification_level"]))
    identity.add_row("MFA Level", str(guild_info["mfa_level"]))
    identity.add_row("Boost Tier", str(guild_info["premium_tier"]))
    identity.add_row("Boost Count", str(guild_info["premium_subscribers"] or 0))
    identity.add_row("Roles", str(report["roles_total"]))
    identity.add_row("Emojis/Stickers", f"{report['emojis_total']}/{report['stickers_total']}")
    console.print(identity)

    table = Table(title="Bot Capability Summary", box=box.HEAVY_HEAD)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")
    table.add_row("Text Channels", str(summary["text_total"]))
    table.add_row("Viewable", str(summary["text_viewable"]))
    table.add_row("Readable History", str(summary["text_history"]))
    table.add_row("Send Allowed", str(summary["text_send"]))
    table.add_row("Invite Allowed", str(summary["text_invite"]))
    table.add_row("Voice Channels", str(summary["voice_total"]))
    table.add_row("Guild Admin", "Y" if bot_info["guild_admin"] else "N")
    table.add_row("Manage Guild", "Y" if bot_info["manage_guild"] else "N")
    table.add_row("Manage Roles", "Y" if bot_info["manage_roles"] else "N")
    console.print(table)

    type_table = Table(title="Channel Type Breakdown", box=box.SIMPLE)
    type_table.add_column("Type", style="cyan")
    type_table.add_column("Count", justify="right")
    for k, v in channel_types.items():
        type_table.add_row(k, str(v))
    console.print(type_table)

    risk_table = Table(title="Permission Risk Flags", box=box.SIMPLE)
    risk_table.add_column("Flag", style="yellow")
    risk_table.add_column("Count", justify="right")
    risk_table.add_row("No View", str(risk_flags["text_no_view_count"]))
    risk_table.add_row("View But No History", str(risk_flags["view_but_no_history_count"]))
    risk_table.add_row("View But No Send", str(risk_flags["view_but_no_send_count"]))
    risk_table.add_row("View But No Invite", str(risk_flags["view_but_no_invite_count"]))
    console.print(risk_table)

    detail = Table(title="Text Channel Detail (first 25)", box=box.MINIMAL)
    detail.add_column("Channel", style="cyan")
    detail.add_column("Category")
    detail.add_column("View")
    detail.add_column("Hist")
    detail.add_column("Send")
    detail.add_column("Invite")
    detail.add_column("NSFW")
    for row in report["channels"][:25]:
        detail.add_row(
            f"#{row['name']}",
            row["category"] or "-",
            "Y" if row["view"] else "N",
            "Y" if row["history"] else "N",
            "Y" if row["send"] else "N",
            "Y" if row["invite"] else "N",
            "Y" if row["nsfw"] else "N",
        )
    console.print(detail)

    if len(report["channels"]) > 25:
        console.print(f"[dim]Showing 25/{len(report['channels'])} text channels. Use export for full data.[/dim]")

    if risk_flags["view_but_no_history"]:
        names = ", ".join(risk_flags["view_but_no_history"][:10])
        if len(risk_flags["view_but_no_history"]) > 10:
            names += f" (+{len(risk_flags['view_but_no_history']) - 10} more)"
        console.print(Panel(names, title="Viewable But No History", border_style="yellow"))


async def export_guild_triage(guild: discord.Guild) -> None:
    report = build_triage_report(guild)
    default_path = f"triage_{guild.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    path = (await ainput(f"Export path [{default_path}]")).strip() or default_path
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    console.print(f"[green]Saved triage report to {path}[/green]")


async def channel_menu(channel: discord.TextChannel) -> None:
    global watch_channel_id, watch_enabled
    show_channel_perms(channel)

    while True:
        console.print(
            Panel(
                "1) Show bot permissions\n"
                "2) Read last 25 messages\n"
                "3) Read last 100 messages\n"
                "4) Toggle LIVE watch\n"
                "5) Send message\n"
                "6) Create invite\n"
                "7) Back",
                title=f"Channel Menu: #{channel.name}",
                border_style="blue",
            )
        )
        choice = (await ainput("Action")).strip()

        if choice == "1":
            show_channel_perms(channel)
        elif choice == "2":
            await fetch_messages(channel, 25)
        elif choice == "3":
            await fetch_messages(channel, 100)
        elif choice == "4":
            if not watch_enabled or watch_channel_id != channel.id:
                watch_channel_id = channel.id
                watch_enabled = True
                console.print("[green]LIVE watch enabled for this channel.[/green]")
            else:
                watch_channel_id = None
                watch_enabled = False
                console.print("[yellow]LIVE watch disabled.[/yellow]")
        elif choice == "5":
            await send_message(channel)
        elif choice == "6":
            await create_invite(channel)
        elif choice == "7":
            watch_channel_id = None
            watch_enabled = False
            return
        else:
            console.print("[yellow]Invalid action.[/yellow]")


async def guild_menu(client: discord.Client, guild: discord.Guild) -> None:
    while True:
        me = guild.me
        summary = guild_summary(guild, me)
        title = Text(f"Guild: {guild.name}", style="bold white")
        subtitle = (
            f"viewable={summary['text_viewable']}/{summary['text_total']}  "
            f"history={summary['text_history']}  send={summary['text_send']}"
        )
        console.print(Panel(subtitle, title=title, border_style="magenta"))

        console.print(
            "[bold]1)[/bold] Select channel  "
            "[bold]2)[/bold] Guild triage  "
            "[bold]3)[/bold] Export triage JSON  "
            "[bold]4)[/bold] Refresh  "
            "[bold]5)[/bold] Back"
        )
        choice = (await ainput("Action")).strip()

        try:
            if choice == "1":
                channel = await choose_channel(guild)
                if channel:
                    await channel_menu(channel)
            elif choice == "2":
                show_guild_triage(guild)
            elif choice == "3":
                await export_guild_triage(guild)
            elif choice == "4":
                try:
                    guild = await client.fetch_guild(guild.id)
                    guild = client.get_guild(guild.id) or guild
                    console.print("[green]Refreshed guild cache.[/green]")
                except Exception as exc:
                    console.print(f"[yellow]Refresh fallback (cache only): {exc}[/yellow]")
            elif choice == "5":
                return
            else:
                console.print("[yellow]Invalid action.[/yellow]")
        except Exception as exc:
            console.print(f"[red]Action failed: {exc}[/red]")


async def run_console(client: discord.Client) -> None:
    while True:
        print_session_overview(client)
        guild = await choose_guild(client)
        if guild is None:
            await client.close()
            return
        await guild_menu(client, guild)


def main() -> None:
    token = Prompt.ask("Use token from env `DISCORD_BOT_TOKEN`?", choices=["y", "n"], default="y")
    if token == "y":
        import os

        env_token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
        if env_token:
            bot_token = env_token
            console.print("[green]Using token from environment.[/green]")
        else:
            console.print("[yellow]Environment token is missing; prompting securely.[/yellow]")
            bot_token = asyncio.run(secure_token_prompt())
    else:
        bot_token = asyncio.run(secure_token_prompt())

    bot_token = bot_token.strip()
    if not bot_token:
        console.print("[red]No token provided.[/red]")
        return

    render_token_triage(bot_token)
    if not Confirm.ask("Connect with this token?", default=True):
        return

    client = build_client()

    @client.event
    async def on_ready() -> None:
        console.print(
            Panel(
                f"Logged in as [bold]{client.user}[/bold] ({client.user.id})\n"
                f"Time: {datetime.now(tz=timezone.utc).isoformat()}",
                title="Connected",
                border_style="green",
            )
        )
        await run_console(client)

    @client.event
    async def on_message(message: discord.Message) -> None:
        if not watch_enabled or watch_channel_id is None:
            return
        if message.channel.id != watch_channel_id:
            return
        if message.author.bot and message.author.id == client.user.id:
            return
        console.print(
            f"[bold cyan][LIVE][/bold cyan] {fmt_dt(message.created_at)} "
            f"[white]{message.author}[/white]: {clip(message.content, 260)}"
        )

    try:
        client.run(bot_token)
    except discord.LoginFailure:
        console.print("[red]Login failed. Token is invalid or not a bot token.[/red]")
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")


if __name__ == "__main__":
    main()
