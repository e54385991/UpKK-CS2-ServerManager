"""
Initialize plugin market with default plugins
This script should be run once to populate the plugin marketplace
"""
import asyncio
import json
from sqlalchemy.ext.asyncio import AsyncSession
from modules.database import async_session_maker
from modules.models import PluginMarketItem


async def populate_plugin_market():
    """Populate plugin market with default plugins"""
    
    plugins = [
        {
            "name": "cs2kz-metamod",
            "display_name": "CS2KZ - Metamod Plugin",
            "category": "功能",  # Functionality
            "description": "CS2KZ (Counter-Strike 2 Kreedz) Metamod plugin provides KZ (climb) gameplay mechanics for CS2 servers. This plugin enables advanced movement features, time tracking, and leaderboards for climb maps.",
            "short_description": "KZ climb gameplay mechanics for CS2 with time tracking and leaderboards",
            "author": "KZGlobalTeam",
            "github_url": "https://github.com/KZGlobalTeam/cs2kz-metamod",
            "related_urls": None,
            "tags": "kz,climb,movement,kreedz,gameplay",
            "icon_url": None,
        },
        {
            "name": "clientcvarvalue",
            "display_name": "Client CVar Value",
            "category": "依赖",  # Dependency
            "description": "ClientCvarValue is a Metamod:Source plugin that allows server administrators to query and monitor client console variables (cvars). This is useful for detecting cheats, enforcing server rules, and monitoring client settings.",
            "short_description": "Query and monitor client console variables on CS2 servers",
            "author": "komashchenko",
            "github_url": "https://github.com/komashchenko/ClientCvarValue/releases",
            "related_urls": None,
            "tags": "cvar,admin,monitoring,anticheat",
            "icon_url": None,
        },
        {
            "name": "multiaddonmanager",
            "display_name": "Multi Addon Manager",
            "category": "功能",  # Functionality
            "description": "Multi Addon Manager (MAM) is a powerful plugin for CS2 servers that allows server administrators to manage multiple addons and maps efficiently. It provides features for automatic addon loading, map rotation, and workshop content management.",
            "short_description": "Manage multiple addons and maps efficiently on CS2 servers",
            "author": "Source2ZE",
            "github_url": "https://github.com/Source2ZE/MultiAddonManager/releases/",
            "related_urls": None,
            "tags": "addon,manager,maps,workshop,administration",
            "icon_url": None,
        },
        {
            "name": "sql_mm",
            "display_name": "SQL MM - MySQL/MariaDB Support",
            "category": "依赖",  # Dependency
            "description": "SQL MM is a Metamod:Source plugin that provides MySQL/MariaDB database connectivity for CS2 server plugins. This is an essential dependency for many advanced plugins that require database storage for statistics, rankings, and persistent data.",
            "short_description": "MySQL/MariaDB database support for CS2 server plugins",
            "author": "zer0k-z",
            "github_url": "https://github.com/zer0k-z/sql_mm/releases",
            "related_urls": None,
            "tags": "sql,mysql,mariadb,database,dependency",
            "icon_url": None,
        },
        {
            "name": "metamod-source",
            "display_name": "Metamod:Source",
            "category": "依赖",  # Dependency
            "description": "Metamod:Source is a plugin/DLL manager that sits between the Half-Life 2 Engine and the game DLL. It allows plugins to intercept calls and modify game behavior without modifying the game DLL itself. This is a fundamental dependency for most CS2 server plugins.",
            "short_description": "Essential plugin loader for Source 2 engine servers",
            "author": "AlliedModders",
            "github_url": "https://github.com/alliedmodders/metamod-source",
            "related_urls": None,
            "tags": "metamod,loader,dependency,core,essential",
            "icon_url": None,
        },
        {
            "name": "counterstrikesharp",
            "display_name": "CounterStrikeSharp",
            "category": "依赖",  # Dependency
            "description": "CounterStrikeSharp is a modern, high-performance plugin framework for Counter-Strike 2 that allows developers to write server plugins in C#. It provides a rich API for game events, commands, and server management. Requires Metamod:Source.",
            "short_description": "Write CS2 server plugins in C# with a modern framework",
            "author": "roflmuffin",
            "github_url": "https://github.com/roflmuffin/CounterStrikeSharp",
            "related_urls": json.dumps(["https://github.com/alliedmodders/metamod-source"]),
            "tags": "csharp,framework,plugin,api,dependency",
            "icon_url": None,
        },
        {
            "name": "cs2fixes",
            "display_name": "CS2Fixes",
            "category": "功能",  # Functionality
            "description": "CS2Fixes is a comprehensive plugin that fixes various bugs and issues in CS2, and adds essential features for competitive and community servers. Includes RTV (Rock The Vote), map voting, team balancing, chat commands, and many other quality of life improvements.",
            "short_description": "Bug fixes and essential features for CS2 competitive servers",
            "author": "Source2ZE",
            "github_url": "https://github.com/Source2ZE/CS2Fixes",
            "related_urls": json.dumps(["https://github.com/alliedmodders/metamod-source"]),
            "tags": "fixes,rtv,voting,admin,competitive,qol",
            "icon_url": None,
        },
        {
            "name": "matchzy",
            "display_name": "MatchZy",
            "category": "功能",  # Functionality
            "description": "MatchZy is a complete match management system for CS2 competitive matches. It provides advanced features for scrims, matches, and tournaments including live config, round restore, backup system, pause functionality, coach system, and detailed statistics.",
            "short_description": "Professional match management system for CS2 competitive play",
            "author": "shobhit-pathak",
            "github_url": "https://github.com/shobhit-pathak/MatchZy",
            "related_urls": json.dumps(["https://github.com/roflmuffin/CounterStrikeSharp", "https://github.com/alliedmodders/metamod-source"]),
            "tags": "match,competitive,tournament,scrim,stats",
            "icon_url": None,
        },
        {
            "name": "openmod",
            "display_name": "OpenMod",
            "category": "娱乐",  # Entertainment
            "description": "OpenMod is a modular plugin framework for CS2 that provides fun and entertaining features for community servers. It includes various mini-games, custom game modes, weapon modifications, and player interaction features to enhance the casual gaming experience.",
            "short_description": "Fun mini-games and custom features for CS2 community servers",
            "author": "OpenMod",
            "github_url": "https://github.com/openmod/openmod",
            "related_urls": None,
            "tags": "fun,minigames,entertainment,custom,community",
            "icon_url": None,
        },
        {
            "name": "simple-admin",
            "display_name": "Simple Admin",
            "category": "功能",  # Functionality
            "description": "Simple Admin is an easy-to-use administration plugin for CS2 servers. It provides essential admin commands for player management, server control, and moderation. Features include kick, ban, mute, slay, teleport, and many other administrative functions.",
            "short_description": "Essential admin commands and moderation tools for CS2 servers",
            "author": "connercsbn",
            "github_url": "https://github.com/connercsbn/SimpleAdmin",
            "related_urls": json.dumps(["https://github.com/roflmuffin/CounterStrikeSharp", "https://github.com/alliedmodders/metamod-source"]),
            "tags": "admin,moderation,commands,management,kick,ban",
            "icon_url": None,
        },
    ]
    
    async with async_session_maker() as session:
        # Check if plugins already exist
        from sqlmodel import select
        result = await session.execute(select(PluginMarketItem))
        existing = result.scalars().all()
        
        if existing:
            print(f"Plugin market already has {len(existing)} plugins. Skipping initialization.")
            return
        
        print(f"Adding {len(plugins)} plugins to the market...")
        for plugin_data in plugins:
            plugin = PluginMarketItem(**plugin_data)
            session.add(plugin)
        
        await session.commit()
        print(f"✓ Successfully added {len(plugins)} plugins to the market!")
        
        # Display summary by category
        result = await session.execute(select(PluginMarketItem))
        all_plugins = result.scalars().all()
        
        from collections import defaultdict
        by_category = defaultdict(list)
        for p in all_plugins:
            by_category[p.category].append(p.display_name)
        
        print("\nPlugins by category:")
        for category, plugin_names in sorted(by_category.items()):
            print(f"  {category}: {len(plugin_names)} plugins")
            for name in plugin_names:
                print(f"    - {name}")


if __name__ == "__main__":
    asyncio.run(populate_plugin_market())
