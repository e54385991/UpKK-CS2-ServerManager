"""
Script to populate the database with sample CounterStrikeSharp plugins
Run this after the database tables are created
"""

import asyncio
import json

from modules import Plugin, PluginCategory, async_session_maker


async def populate_sample_plugins():
    """Add sample plugins to the database"""

    sample_plugins = [
        # Utility Plugins
        {
            "name": "admin_management",
            "display_name": "Admin Management",
            "description": "Comprehensive admin management system with permission levels, group management, and command restrictions.",
            "category": PluginCategory.ADMIN,
            "version": "1.2.0",
            "download_url": "https://github.com/example/cs2-admin-management/releases/download/v1.2.0/admin_management.tar.gz",
            "author": "CS2 Community",
            "homepage": "https://github.com/example/cs2-admin-management",
            "dependencies": None,
            "install_path": "addons/counterstrikesharp/plugins",
            "config_required": True,
            "enabled": True,
        },
        {
            "name": "teleport_manager",
            "display_name": "Teleport Manager",
            "description": "Allows admins to teleport players, save locations, and create teleport zones.",
            "category": PluginCategory.UTILITY,
            "version": "1.0.5",
            "download_url": "https://github.com/example/cs2-teleport/releases/download/v1.0.5/teleport_manager.tar.gz",
            "author": "TeleportDev",
            "homepage": "https://github.com/example/cs2-teleport",
            "dependencies": json.dumps([1]),  # Depends on admin_management (ID will be 1)
            "install_path": "addons/counterstrikesharp/plugins",
            "config_required": False,
            "enabled": True,
        },
        {
            "name": "player_stats",
            "display_name": "Player Statistics",
            "description": "Track and display player statistics including kills, deaths, headshots, accuracy, and more.",
            "category": PluginCategory.STATISTICS,
            "version": "2.1.0",
            "download_url": "https://github.com/example/cs2-stats/releases/download/v2.1.0/player_stats.tar.gz",
            "author": "StatsTeam",
            "homepage": "https://github.com/example/cs2-stats",
            "dependencies": None,
            "install_path": "addons/counterstrikesharp/plugins",
            "config_required": True,
            "enabled": True,
        },
        # Chat Plugins
        {
            "name": "enhanced_chat",
            "display_name": "Enhanced Chat",
            "description": "Enhanced chat features with custom colors, tags, chat filters, and anti-spam.",
            "category": PluginCategory.CHAT,
            "version": "1.5.2",
            "download_url": "https://github.com/example/cs2-enhanced-chat/releases/download/v1.5.2/enhanced_chat.tar.gz",
            "author": "ChatMods",
            "homepage": "https://github.com/example/cs2-enhanced-chat",
            "dependencies": None,
            "install_path": "addons/counterstrikesharp/plugins",
            "config_required": True,
            "enabled": True,
        },
        {
            "name": "chat_translator",
            "display_name": "Chat Translator",
            "description": "Automatically translate chat messages between different languages for international servers.",
            "category": PluginCategory.CHAT,
            "version": "1.0.0",
            "download_url": "https://github.com/example/cs2-translator/releases/download/v1.0.0/chat_translator.tar.gz",
            "author": "TranslateTeam",
            "homepage": "https://github.com/example/cs2-translator",
            "dependencies": None,
            "install_path": "addons/counterstrikesharp/plugins",
            "config_required": True,
            "enabled": True,
        },
        # Gameplay Plugins
        {
            "name": "deathmatch",
            "display_name": "Deathmatch Mode",
            "description": "Full-featured deathmatch mode with instant respawn, weapon selection, and spawn protection.",
            "category": PluginCategory.GAMEPLAY,
            "version": "3.0.1",
            "download_url": "https://github.com/example/cs2-deathmatch/releases/download/v3.0.1/deathmatch.tar.gz",
            "author": "DMTeam",
            "homepage": "https://github.com/example/cs2-deathmatch",
            "dependencies": None,
            "install_path": "addons/counterstrikesharp/plugins",
            "config_required": True,
            "enabled": True,
        },
        {
            "name": "retakes",
            "display_name": "Retakes Mode",
            "description": "Practice retake scenarios with automatic team balancing and bombsite selection.",
            "category": PluginCategory.GAMEPLAY,
            "version": "2.5.0",
            "download_url": "https://github.com/example/cs2-retakes/releases/download/v2.5.0/retakes.tar.gz",
            "author": "RetakesTeam",
            "homepage": "https://github.com/example/cs2-retakes",
            "dependencies": None,
            "install_path": "addons/counterstrikesharp/plugins",
            "config_required": True,
            "enabled": True,
        },
        {
            "name": "aim_training",
            "display_name": "Aim Training",
            "description": "Aim training maps and challenges with target practice, flick shots, and tracking exercises.",
            "category": PluginCategory.GAMEPLAY,
            "version": "1.3.0",
            "download_url": "https://github.com/example/cs2-aim-training/releases/download/v1.3.0/aim_training.tar.gz",
            "author": "AimTraining",
            "homepage": "https://github.com/example/cs2-aim-training",
            "dependencies": None,
            "install_path": "addons/counterstrikesharp/plugins",
            "config_required": False,
            "enabled": True,
        },
        # Cosmetic Plugins
        {
            "name": "player_models",
            "display_name": "Custom Player Models",
            "description": "Allow players to choose from various player models and customize their appearance.",
            "category": PluginCategory.COSMETIC,
            "version": "1.1.0",
            "download_url": "https://github.com/example/cs2-player-models/releases/download/v1.1.0/player_models.tar.gz",
            "author": "ModelTeam",
            "homepage": "https://github.com/example/cs2-player-models",
            "dependencies": None,
            "install_path": "addons/counterstrikesharp/plugins",
            "config_required": True,
            "enabled": True,
        },
        {
            "name": "weapon_skins",
            "display_name": "Weapon Skins Manager",
            "description": "Give players access to weapon skins without Steam inventory integration.",
            "category": PluginCategory.COSMETIC,
            "version": "2.0.0",
            "download_url": "https://github.com/example/cs2-weapon-skins/releases/download/v2.0.0/weapon_skins.tar.gz",
            "author": "SkinsTeam",
            "homepage": "https://github.com/example/cs2-weapon-skins",
            "dependencies": None,
            "install_path": "addons/counterstrikesharp/plugins",
            "config_required": True,
            "enabled": True,
        },
        # Utility Plugins
        {
            "name": "map_manager",
            "display_name": "Map Manager",
            "description": "Advanced map management with votemap, mapcycle, workshop maps support, and map nominations.",
            "category": PluginCategory.UTILITY,
            "version": "2.2.0",
            "download_url": "https://github.com/example/cs2-map-manager/releases/download/v2.2.0/map_manager.tar.gz",
            "author": "MapTeam",
            "homepage": "https://github.com/example/cs2-map-manager",
            "dependencies": None,
            "install_path": "addons/counterstrikesharp/plugins",
            "config_required": True,
            "enabled": True,
        },
        {
            "name": "team_balancer",
            "display_name": "Team Balancer",
            "description": "Automatically balance teams based on player skill, KDR, or round performance.",
            "category": PluginCategory.UTILITY,
            "version": "1.4.0",
            "download_url": "https://github.com/example/cs2-team-balancer/releases/download/v1.4.0/team_balancer.tar.gz",
            "author": "BalanceTeam",
            "homepage": "https://github.com/example/cs2-team-balancer",
            "dependencies": json.dumps([3]),  # Depends on player_stats
            "install_path": "addons/counterstrikesharp/plugins",
            "config_required": True,
            "enabled": True,
        },
        {
            "name": "warmup_config",
            "display_name": "Warmup Configuration",
            "description": "Enhanced warmup with custom time, infinite ammo, and practice utilities.",
            "category": PluginCategory.UTILITY,
            "version": "1.0.3",
            "download_url": "https://github.com/example/cs2-warmup/releases/download/v1.0.3/warmup_config.tar.gz",
            "author": "WarmupDev",
            "homepage": "https://github.com/example/cs2-warmup",
            "dependencies": None,
            "install_path": "addons/counterstrikesharp/plugins",
            "config_required": True,
            "enabled": True,
        },
        # Admin Tools
        {
            "name": "ban_system",
            "display_name": "Ban System",
            "description": "Advanced ban system with temporary bans, IP bans, and ban history tracking.",
            "category": PluginCategory.ADMIN,
            "version": "1.6.0",
            "download_url": "https://github.com/example/cs2-ban-system/releases/download/v1.6.0/ban_system.tar.gz",
            "author": "AdminTools",
            "homepage": "https://github.com/example/cs2-ban-system",
            "dependencies": json.dumps([1]),  # Depends on admin_management
            "install_path": "addons/counterstrikesharp/plugins",
            "config_required": True,
            "enabled": True,
        },
        {
            "name": "vote_kick",
            "display_name": "Vote Kick",
            "description": "Allow players to vote to kick disruptive players with configurable thresholds.",
            "category": PluginCategory.ADMIN,
            "version": "1.2.1",
            "download_url": "https://github.com/example/cs2-votekick/releases/download/v1.2.1/vote_kick.tar.gz",
            "author": "VoteTeam",
            "homepage": "https://github.com/example/cs2-votekick",
            "dependencies": None,
            "install_path": "addons/counterstrikesharp/plugins",
            "config_required": True,
            "enabled": True,
        },
        # Other
        {
            "name": "rank_system",
            "display_name": "Rank System",
            "description": "Custom ranking system with XP, levels, and rank display based on performance.",
            "category": PluginCategory.OTHER,
            "version": "1.8.0",
            "download_url": "https://github.com/example/cs2-rank-system/releases/download/v1.8.0/rank_system.tar.gz",
            "author": "RankTeam",
            "homepage": "https://github.com/example/cs2-rank-system",
            "dependencies": json.dumps([3]),  # Depends on player_stats
            "install_path": "addons/counterstrikesharp/plugins",
            "config_required": True,
            "enabled": True,
        },
        {
            "name": "economy_system",
            "display_name": "Economy System",
            "description": "Virtual economy with currency, shop, and rewards for player actions.",
            "category": PluginCategory.OTHER,
            "version": "2.0.5",
            "download_url": "https://github.com/example/cs2-economy/releases/download/v2.0.5/economy_system.tar.gz",
            "author": "EconTeam",
            "homepage": "https://github.com/example/cs2-economy",
            "dependencies": None,
            "install_path": "addons/counterstrikesharp/plugins",
            "config_required": True,
            "enabled": True,
        },
    ]

    async with async_session_maker() as db:
        # Check if plugins already exist
        from sqlmodel import select

        result = await db.execute(select(Plugin))
        existing_plugins = result.scalars().all()

        if existing_plugins:
            print(
                f"Database already contains {len(existing_plugins)} plugins. Skipping population."
            )
            return

        print("Adding sample plugins to database...")

        for plugin_data in sample_plugins:
            plugin = Plugin(**plugin_data)
            db.add(plugin)

        await db.commit()
        print(f"✓ Successfully added {len(sample_plugins)} sample plugins to the database!")


if __name__ == "__main__":
    asyncio.run(populate_sample_plugins())
