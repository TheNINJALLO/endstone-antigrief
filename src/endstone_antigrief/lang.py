# Language file for AntiGrief Plugin - English Only
# Version 1.2.0

lang = {
    "lang-version": "1.2.0",
    "language": "English",
    
    # Command descriptions
    "cmd_ty_desc": "Query player & entity behavior records -- Format: /ty x y z time(hours) radius",
    "cmd_tyhelp_desc": "View AntiGrief command help",
    "cmd_tyban_desc": "Ban a player (OP only)",
    "cmd_tyunban_desc": "Remove a player from the blacklist (OP only)",
    "cmd_tybanlist_desc": "List all blacklisted players (OP only)",
    "cmd_banid_desc": "Ban a device ID (OP only)",
    "cmd_unbanid_desc": "Remove a device ID from the blacklist (OP only)",
    "cmd_banidlist_desc": "List all blacklisted device IDs (OP only)",
    "cmd_tys_desc": "Keyword search -- Format: /tys type keyword time(hours) (OP only)",
    "cmd_tyback_desc": "Restore block placement/destruction and explosion damage -- Format: /tyback pos time(hours) radius [player/entity] (OP only)",
    "cmd_tyo_desc": "View player inventory -- Format: /tyo playername",
    "cmd_tyclean_desc": "Clean database records older than specified time -- Format: /tyclean time(hours) (OP only)",
    "cmd_density_desc": "Detect the area with highest entity density -- Format: /density area_size (OP only)",
    
    # Plugin status messages
    "plugin_enabled": "AntiGrief enabled - Version",
    "config_location": "Config file located at",
    "data_location": "Data files located at",
    "project_url": "Project URL",
    
    # Help messages
    "help_title": "AntiGrief Command Usage",
    "help_tyban": "Use /tyban <player> [reason] to blacklist a player",
    "help_tyunban": "Use /tyunban <player> to remove a player from the blacklist",
    "help_banlist": "Use /tybanlist to list all blacklisted players",
    "help_banid": "Use /ban-id <deviceID> to blacklist a device (cannot kick if online, kick manually first)",
    "help_unbanid": "Use /unban-id <deviceID> to remove a device from the blacklist",
    "help_banidlist": "Use /banlist-id to list all blacklisted device IDs",
    "help_ty": "Use /ty x y z time radius to query behavior records. Without args, opens GUI menu.",
    "help_tys": "Use /tys type keyword time for keyword search. Types: player, action, object. Without args, opens GUI menu.",
    "help_tyback": "Use /tyback pos time radius [player/entity] to restore changes (experimental)",
    "help_tyo": "Use /tyo <player> to view player inventory",
    "help_tyclean": "Use /tyclean <hours> to clean old database records",
    "help_density": "Use /density <size> to find highest entity density area",
    
    # Error messages
    "error_format": "Command format error! Please check your input",
    "error_radius_max": "Maximum query radius is 100!",
    "error_no_results": "No results found.",
    "error_unknown_param": "Command format error! Unknown parameter",
    "error_console_only": "This command cannot be used from console",
    "error_invalid_params": "Invalid parameters",
    "error_player_offline": "Error: Player not online or invalid parameters",
    "error_db_clean": "An unknown error occurred during database cleaning",
    
    # Query results
    "query_found": "Found records for coordinate radius",
    "query_keyword_found": "Found records for keyword",
    "query_blocks": "blocks",
    "query_hours": "hours",
    "query_behavior_records": "player & entity behavior records",
    "query_see_popup": "Please check the popup window",
    "query_page": "Page",
    "query_no_data": "No record data",
    
    # Data labels
    "label_actor": "Actor",
    "label_action": "Action",
    "label_coordinates": "Coordinates",
    "label_time": "Time",
    "label_object_type": "Object Type",
    "label_dimension": "Dimension",
    "label_radius": "Radius",
    "label_keyword": "Keyword",
    
    # Pagination
    "next_page": "Next Page",
    "prev_page": "Previous Page",
    
    # Ban system
    "format_error": "Format error",
    "player": "Player",
    "already_banned": "is already in the blacklist since",
    "no_duplicate": "Do not add duplicates",
    "banned_reason": "has been blacklisted. Reason:",
    "reason": "Reason",
    "blacklist_created": "Blacklist file created automatically",
    "removed_from_blacklist": "removed from the blacklist",
    "not_in_blacklist": "is not in the blacklist",
    "blacklist_not_exist": "Blacklist file does not exist",
    "no_banned_players": "No players in the blacklist",
    "banned_at": "on",
    "banned": "Banned",
    
    # Device ban
    "device_id": "Device ID",
    "device_already_banned": "is already in the device blacklist, do not add duplicates",
    "device_banned": "has been added to the blacklist",
    "device_blacklist_created": "Device blacklist file created automatically",
    "device_blacklist_not_exist": "Device blacklist file does not exist",
    "no_banned_devices": "No devices in the blacklist",
    
    # Anti-spam
    "spam_cmd_ban": "You have been banned for sending too many commands in a short time",
    "spam_msg_ban": "You have been banned for sending too many messages in a short time",
    "spam_cmd_notify": "Banned for command spam",
    "spam_msg_notify": "Banned for message spam",
    
    # Player join/kick
    "you_are_banned": "You have been banned. Reason:",
    "ban_time": "Ban time",
    "kicked_banned": "is on the ban list and has been kicked. Reason:",
    "device_banned_at": "Your device was banned on",
    "banned_device_tried": "Banned device attempted to join, kicked",
    "system_name": "System name",
    "joined_game": "joined the game",
    
    # Inventory
    "item_slot": "Slot",
    "item_name": "Item",
    "item_quantity": "Qty",
    "inventory_of": "'s Inventory",
    "inventory_empty": "This player's inventory is empty",
    
    # Database cleanup
    "db_clean_complete": "Cleanup complete, database organized",
    "db_error": "Error occurred",
    "db_deleted": "Deleted",
    "db_data_older": "data older than",
    "db_restructured": "Database restructured to free space",
    "db_lines": "lines",
    
    # Rollback
    "rollback_start": "Starting restoration for",
    "rollback_blocks": "blocks within",
    
    # Entity density
    "density_default": "No size provided, using default 20 blocks",
    "density_dimension": "Highest density area is in dimension",
    "density_midpoint": "Midpoint coordinates",
    "density_count": "Entity count",
    "density_most_common": "Most common entity",
    "density_random_pos": "Sample position",
    "density_none": "No entities detected",
    "density_results": "Entity Density Results",
    "density_teleport": "Teleport to Area",
    "density_print": "Print to Chat",
    
    # GUI Forms
    "gui_query_title": "AntiGrief Query Menu",
    "gui_enter_coords": "Enter query coordinates",
    "gui_enter_time": "Enter query time (hours)",
    "gui_enter_radius": "Enter query radius",
    "gui_search_title": "AntiGrief Keyword Search",
    "gui_search_type": "Select search type (player, action, object)",
    "gui_enter_keyword": "Enter search keyword",
    
    # Actions
    "action_interact": "Interact",
    "action_break": "Break",
    "action_place": "Place",
    "action_attack": "Attack",
    "action_explode": "Explode",
    "action_piston_push": "Piston Push",
    "action_piston_pull": "Piston Pull",
    
    # WebUI
    "webui_started": "WebUI started on port",
}
