import { world, system, ItemStack, Enchantment } from "@minecraft/server";

const NS = "antigrief";

// Serialize an item stack to a compact JSON representation
function serializeItemStack(item, slot) {
    if (!item) return null;
    const data = {
        s: slot,
        t: item.typeId,
        a: item.amount
    };
    if (item.nameTag) {
        data.n = item.nameTag;
    }
    const lore = item.getLore();
    if (lore && lore.length > 0) {
        data.l = lore;
    }
    
    // Enchantments
    try {
        const enchantable = item.getComponent("minecraft:enchantable");
        if (enchantable) {
            const enchants = enchantable.getEnchantments();
            if (enchants && enchants.length > 0) {
                data.e = enchants.map(e => ({ t: e.type.id, l: e.level }));
            }
        }
    } catch (err) {
        // Enchantment component might not be supported on some item types
    }
    return data;
}

// Helper to check if block is a container and send its backup
function backupContainer(block, dimensionName) {
    try {
        const inventoryComp = block.getComponent("minecraft:inventory");
        if (inventoryComp && inventoryComp.container) {
            const container = inventoryComp.container;
            const items = [];
            for (let i = 0; i < container.size; i++) {
                const item = container.getItem(i);
                if (item) {
                    const serialized = serializeItemStack(item, i);
                    if (serialized) {
                        items.push(serialized);
                    }
                }
            }
            
            // Only send backup if there are items inside
            if (items.length > 0) {
                const payload = JSON.stringify({
                    x: block.x,
                    y: block.y,
                    z: block.z,
                    dim: dimensionName,
                    items: items
                });
                
                // Run command to notify Python
                // Since this runs inside a beforeEvent, it is synchronous
                const dimObj = world.getDimension(dimensionName);
                dimObj.runCommand(`scriptevent ${NS}:container_backup ${payload}`);
            }
        }
    } catch (e) {
        console.warn(`[AntiGrief BP] Failed to backup container at ${block.x},${block.y},${block.z}: ${e}`);
    }
}

// 1. Subscribe to playerBreakBlock (beforeEvent)
world.beforeEvents.playerBreakBlock.subscribe((event) => {
    const block = event.block;
    const player = event.player;
    backupContainer(block, player.dimension.id);
});

// 2. Subscribe to explosion (beforeEvent)
world.beforeEvents.explosion.subscribe((event) => {
    try {
        const impactedBlocks = event.getImpactedBlocks();
        const dimension = event.dimension;
        for (const block of impactedBlocks) {
            const inventoryComp = block.getComponent("minecraft:inventory");
            if (inventoryComp) {
                backupContainer(block, dimension.id);
            }
        }
    } catch (e) {
        console.warn(`[AntiGrief BP] Error in explosion handling: ${e}`);
    }
});

// 3. Listen for restores from Python
system.afterEvents.scriptEventReceive.subscribe((event) => {
    if (event.id === `${NS}:container_restore`) {
        try {
            const data = JSON.parse(event.message);
            const x = data.x;
            const y = data.y;
            const z = data.z;
            const dimName = data.dim;
            const items = data.items || [];
            const clear = data.clear || false;
            
            const dimension = world.getDimension(dimName);
            const block = dimension.getBlock({ x, y, z });
            if (!block) return;
            
            const inventoryComp = block.getComponent("minecraft:inventory");
            if (inventoryComp && inventoryComp.container) {
                const container = inventoryComp.container;
                
                if (clear) {
                    for (let i = 0; i < container.size; i++) {
                        container.setItem(i, undefined);
                    }
                }
                
                for (const itemData of items) {
                    try {
                        const itemStack = new ItemStack(itemData.t, itemData.a);
                        
                        // Restore custom nameTag
                        if (itemData.n) {
                            itemStack.nameTag = itemData.n;
                        }
                        
                        // Restore lore
                        if (itemData.l) {
                            itemStack.setLore(itemData.l);
                        }
                        
                        // Restore enchantments
                        if (itemData.e) {
                            const enchantable = itemStack.getComponent("minecraft:enchantable");
                            if (enchantable) {
                                for (const enchantData of itemData.e) {
                                    try {
                                        const enchant = new Enchantment(enchantData.t, enchantData.l);
                                        enchantable.addEnchantment(enchant);
                                    } catch (ee) {
                                        // Ignore incompatible enchantment conflicts
                                    }
                                }
                            }
                        }
                        
                        container.setItem(itemData.s, itemStack);
                    } catch (itemErr) {
                        console.warn(`[AntiGrief BP] Failed to restore item stack at slot ${itemData.s}: ${itemErr}`);
                    }
                }
            }
        } catch (e) {
            console.warn(`[AntiGrief BP] Failed to process container_restore event: ${e}`);
        }
    }
});

console.warn("[AntiGrief BP] Loaded successfully!");
