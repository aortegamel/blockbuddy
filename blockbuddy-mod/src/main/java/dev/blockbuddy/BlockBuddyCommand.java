package dev.blockbuddy;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.mojang.brigadier.CommandDispatcher;
import com.mojang.brigadier.arguments.StringArgumentType;
import com.mojang.brigadier.exceptions.CommandSyntaxException;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.core.BlockPos;
import net.minecraft.core.NonNullList;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.crafting.CraftingRecipe;
import net.minecraft.world.item.crafting.Ingredient;
import net.minecraft.world.item.crafting.RecipeHolder;
import net.minecraft.world.item.crafting.RecipeType;
import net.minecraft.world.level.block.entity.BlockEntity;
import net.neoforged.neoforge.capabilities.Capabilities;
import net.neoforged.neoforge.items.IItemHandler;
import net.neoforged.neoforge.items.ItemHandlerHelper;

import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * /blockbuddy spawn [name]  — summon a built-in companion next to the caller
 * /blockbuddy run <goto x y z|follow|stop> — steer the nearest companion
 *                              (vanilla pathfinding, no external pathing mod)
 * /blockbuddy mine <block> [n] — break nearest matching blocks in radius,
 *                              including modded ids (kubejs:dead_log 16)
 * /blockbuddy state         — one-line JSON status (pos/health/inventory),
 *                              echoed to chat for the daemon's chat_log reader
 * /blockbuddy stop          — shortcut for run stop
 *
 * (/agent stays as a short alias.)
 *
 * The external planner daemon issues these through the Minescript chat bridge,
 * so the LLM brain stays outside the game while the built-in FakePlayer-like
 * body (BlockBuddyEntity) does the work.
 */
public final class BlockBuddyCommand {

    private BlockBuddyCommand() {}

    public static void register(CommandDispatcher<CommandSourceStack> dispatcher) {
        var root = Commands.literal("blockbuddy")
            .then(Commands.literal("spawn")
                .requires(src -> src.getServer().isSingleplayer() || src.hasPermission(2))
                .executes(ctx -> spawnNamed(ctx.getSource(), null, true))
                .then(Commands.argument("name", StringArgumentType.word())
                    .executes(ctx -> spawnNamed(ctx.getSource(),
                            StringArgumentType.getString(ctx, "name"), false))))
            .then(Commands.literal("run")
                .then(Commands.argument("cmd", StringArgumentType.greedyString())
                    .executes(ctx -> run(ctx.getSource(),
                            StringArgumentType.getString(ctx, "cmd")))))
            .then(Commands.literal("mine")
                .then(Commands.argument("spec", StringArgumentType.greedyString())
                    .executes(ctx -> mine(ctx.getSource(),
                            StringArgumentType.getString(ctx, "spec")))))
            .then(Commands.literal("give")
                .then(Commands.argument("spec", StringArgumentType.greedyString())
                    .executes(ctx -> give(ctx.getSource(),
                            StringArgumentType.getString(ctx, "spec")))))
            .then(Commands.literal("craft")
                .then(Commands.argument("spec", StringArgumentType.greedyString())
                    .executes(ctx -> craft(ctx.getSource(),
                            StringArgumentType.getString(ctx, "spec")))))
            .then(Commands.literal("take")
                .then(Commands.argument("spec", StringArgumentType.greedyString())
                    .executes(ctx -> container(ctx.getSource(),
                            StringArgumentType.getString(ctx, "spec"), true))))
            .then(Commands.literal("store")
                .then(Commands.argument("spec", StringArgumentType.greedyString())
                    .executes(ctx -> container(ctx.getSource(),
                            StringArgumentType.getString(ctx, "spec"), false))))
            .then(Commands.literal("state")
                .executes(ctx -> state(ctx.getSource())))
            .then(Commands.literal("voice")
                .requires(src -> src.getServer().isSingleplayer() || src.hasPermission(2))
                .executes(ctx -> voice(ctx.getSource()))
                .then(Commands.argument("option", StringArgumentType.word())
                    .executes(ctx -> voice(ctx.getSource()))))
            .then(Commands.literal("brain")
                .then(Commands.literal("on")
                    .executes(ctx -> brain(ctx.getSource(), true)))
                .then(Commands.literal("off")
                    .executes(ctx -> brain(ctx.getSource(), false))))
            .then(Commands.literal("stop")
                .executes(ctx -> run(ctx.getSource(), "stop")))
            .then(Commands.literal("reconnect")
                .executes(ctx -> reconnect(ctx.getSource())));
        var node = dispatcher.register(root);
        dispatcher.register(Commands.literal("agent").redirect(node));
    }

    /** Max live companions per world. */
    private static final int MAX_BUDDIES = 16;

    private static List<BlockBuddyEntity> allBuddies(ServerPlayer player) {
        return player.serverLevel().getEntitiesOfClass(
                BlockBuddyEntity.class, player.getBoundingBox().inflate(1024.0));
    }

    private static BlockBuddyEntity nearest(ServerPlayer player) {
        List<BlockBuddyEntity> agents = player.serverLevel().getEntitiesOfClass(
                BlockBuddyEntity.class, player.getBoundingBox().inflate(96.0));
        return agents.stream()
                .min(Comparator.comparingDouble(e -> e.distanceToSqr(player)))
                .orElse(null);
    }

    private static BlockBuddyEntity requireBuddy(CommandSourceStack src, ServerPlayer player) {
        BlockBuddyEntity agent = nearest(player);
        if (agent == null) {
            src.sendFailure(Component.literal("[blockbuddy] no agent within 96 blocks — /blockbuddy spawn first"));
        }
        return agent;
    }

    private static String[] arguments(String spec) {
        return spec == null || spec.isBlank() ? new String[0] : spec.trim().split("\\s+");
    }

    private static int count(String[] parts, int fallback, boolean allowAll) {
        if (parts.length == 1) return fallback;
        if (parts.length != 2) return -1;
        if (allowAll && parts[1].equals("all")) return Integer.MAX_VALUE;
        try {
            int value = Integer.parseInt(parts[1]);
            return value > 0 ? value : -1;
        } catch (NumberFormatException e) {
            return -1;
        }
    }

    private static int invalid(BlockBuddyEntity agent, String message) {
        agent.reportEvent("invalid", "", 0, false, message);
        return 0;
    }

    private static int mine(CommandSourceStack src, String spec) throws CommandSyntaxException {
        ServerPlayer player = src.getPlayerOrException();
        BlockBuddyEntity agent = requireBuddy(src, player);
        return agent == null ? 0 : mine(player, agent, spec);
    }

    public static int mine(ServerPlayer player, BlockBuddyEntity agent, String spec) {
        agent.hearCommand();
        String[] parts = arguments(spec);
        int wanted = count(parts, 8, false);
        if (wanted < 1) return invalid(agent, "Use mine <block or #tag> [positive count].");
        boolean ok = agent.startMining(parts[0], wanted);
        agent.reportEvent("mine_started", parts[0], wanted, ok,
                ok ? "Mining " + wanted + " " + parts[0] + "."
                        : "I cannot mine that block or tag: " + parts[0] + ".");
        return ok ? 1 : 0;
    }

    private static int give(CommandSourceStack src, String spec) throws CommandSyntaxException {
        ServerPlayer player = src.getPlayerOrException();
        BlockBuddyEntity agent = requireBuddy(src, player);
        return agent == null ? 0 : give(player, agent, spec);
    }

    /** Transfer only the inventory items that match the selected item or tool group. */
    public static int give(ServerPlayer player, BlockBuddyEntity agent, String spec) {
        agent.hearCommand();
        String[] parts = arguments(spec);
        int wanted = count(parts, 64, true);
        if (wanted < 1) return invalid(agent, "Use give <item> [positive count or all].");
        if (agent.distanceToSqr(player) > 64.0) {
            return invalid(agent, "I must be within 8 blocks. Use /blockbuddy run follow.");
        }
        String selector = parts[0];
        if (ambiguousInventorySelector(agent, selector)) {
            return invalid(agent, "That item name is ambiguous. Use a namespaced item ID.");
        }
        int moved = 0;
        for (ItemStack stack : agent.inv) {
            if (moved >= wanted || stack.isEmpty() || !matchesInventory(stack, selector)) continue;
            // ponytail: size the offer to main-inventory room, mirroring Inventory.add;
            // the accepted remainder below is the only thing that shrinks the buddy.
            int take = Math.min(wanted - moved, stack.getCount());
            take = Math.min(take, playerRoomFor(player, stack));
            if (take <= 0) continue;
            ItemStack transfer = stack.copyWithCount(take);
            player.getInventory().add(transfer);
            int accepted = take - transfer.getCount();
            if (accepted > 0) {
                stack.shrink(accepted);
                moved += accepted;
            }
        }
        agent.reportEvent("give", selector, moved, moved > 0,
                moved > 0 ? "Here are " + moved + " " + selector + "."
                        : "I could not give you any " + selector + ".");
        return moved > 0 ? 1 : 0;
    }

    private static boolean toolSelector(String selector) {
        return switch (selector) {
            case "sword", "pickaxe", "axe", "shovel", "hoe" -> true;
            default -> false;
        };
    }

    private static boolean matchesInventory(ItemStack stack, String selector) {
        ResourceLocation key = BuiltInRegistries.ITEM.getKey(stack.getItem());
        if (selector.contains(":")) return key.toString().equals(selector);
        if (toolSelector(selector)) return key.getPath().endsWith("_" + selector);
        return key.getPath().equals(selector);
    }

    private static int playerRoomFor(ServerPlayer player, ItemStack stack) {
        // ponytail: mirror vanilla Inventory.add exactly — main 36 slots only.
        // Pickup never fills armor, offhand, crafting, or result slots.
        int room = 0;
        for (ItemStack slot : player.getInventory().items) {
            if (slot.isEmpty()) room += stack.getMaxStackSize();
            else if (ItemStack.isSameItemSameComponents(slot, stack) && slot.getCount() < slot.getMaxStackSize()) {
                room += slot.getMaxStackSize() - slot.getCount();
            }
        }
        return room;
    }

    private static boolean ambiguousInventorySelector(BlockBuddyEntity agent, String selector) {
        if (selector.contains(":") || toolSelector(selector)) return false;
        Item found = null;
        for (ItemStack stack : agent.inv) {
            if (stack.isEmpty() || !matchesInventory(stack, selector)) continue;
            if (found != null && found != stack.getItem()) return true;
            found = stack.getItem();
        }
        return false;
    }

    private static Item resolveItem(String name) {
        ResourceLocation id = ResourceLocation.tryParse(name);
        if (name.contains(":")) {
            return id != null && BuiltInRegistries.ITEM.containsKey(id) ? BuiltInRegistries.ITEM.get(id) : null;
        }
        Item found = null;
        for (ResourceLocation key : BuiltInRegistries.ITEM.keySet()) {
            if (!key.getPath().equals(name)) continue;
            if (found != null) return null;
            found = BuiltInRegistries.ITEM.get(key);
        }
        return found;
    }

    private static int insertIntoAgent(BlockBuddyEntity agent, ItemStack stack) {
        return agent.insertStack(stack);
    }

    private static int countIn(NonNullList<ItemStack> inv, Item item) {
        int total = 0;
        for (ItemStack stack : inv) {
            if (!stack.isEmpty() && stack.getItem() == item) total += stack.getCount();
        }
        return total;
    }

    private static int inventoryRoom(BlockBuddyEntity agent, ItemStack stack) {
        int room = 0;
        for (ItemStack slot : agent.inv) {
            if (slot.isEmpty()) room += stack.getMaxStackSize();
            else if (ItemStack.isSameItemSameComponents(slot, stack)) {
                room += Math.max(0, slot.getMaxStackSize() - slot.getCount());
            }
        }
        return room;
    }

    private static boolean tryCraft(BlockBuddyEntity agent, CraftingRecipe recipe, ItemStack result) {
        NonNullList<ItemStack> inv = agent.inv;
        int[] counts = new int[inv.size()];
        for (int i = 0; i < inv.size(); i++) counts[i] = inv.get(i).getCount();
        for (Ingredient ing : recipe.getIngredients()) {
            if (ing.isEmpty()) continue;
            boolean matched = false;
            for (int i = 0; i < inv.size(); i++) {
                if (counts[i] > 0 && ing.test(inv.get(i))) {
                    counts[i]--;
                    matched = true;
                    break;
                }
            }
            if (!matched) return false;
        }
        for (int i = 0; i < inv.size(); i++) {
            inv.get(i).shrink(inv.get(i).getCount() - counts[i]);
        }
        if (inventoryRoom(agent, result) < result.getCount()) return false;
        return insertIntoAgent(agent, result) == result.getCount();
    }

    /** Resolve intermediate recipes against all ingredient alternatives. Failed recipes restore inventory. */
    private static boolean craftRecursive(ServerPlayer player, BlockBuddyEntity agent,
                                          Item target, int wanted, int depth) {
        NonNullList<ItemStack> inv = agent.inv;
        if (countIn(inv, target) >= wanted) return true;
        if (depth <= 0 || wanted > inv.size() * new ItemStack(target).getMaxStackSize()) return false;
        List<RecipeHolder<CraftingRecipe>> recipes = player.serverLevel().getRecipeManager()
                .getAllRecipesFor(RecipeType.CRAFTING);
        for (RecipeHolder<CraftingRecipe> holder : recipes) {
            CraftingRecipe recipe = holder.value();
            ItemStack result = recipe.getResultItem(player.serverLevel().registryAccess());
            if (result.isEmpty() || result.getItem() != target) continue;
            List<ItemStack> before = inv.stream().map(ItemStack::copy).toList();
            boolean ok = true;
            while (ok && countIn(inv, target) < wanted) {
                Map<Item, Integer> reserved = new HashMap<>();
                for (Ingredient ing : recipe.getIngredients()) {
                    if (ing.isEmpty()) continue;
                    Item selected = null;
                    for (ItemStack stack : inv) {
                        if (!stack.isEmpty() && ing.test(stack)
                                && countIn(inv, stack.getItem()) > reserved.getOrDefault(stack.getItem(), 0)) {
                            selected = stack.getItem();
                            break;
                        }
                    }
                    if (selected == null) {
                        for (ItemStack option : ing.getItems()) {
                            Item item = option.getItem();
                            if (item != target && craftRecursive(player, agent, item,
                                    reserved.getOrDefault(item, 0) + 1, depth - 1)) {
                                selected = item;
                                break;
                            }
                        }
                    }
                    if (selected == null) {
                        ok = false;
                        break;
                    }
                    reserved.merge(selected, 1, Integer::sum);
                }
                int previous = countIn(inv, target);
                if (ok) ok = tryCraft(agent, recipe, result) && countIn(inv, target) > previous;
            }
            if (ok) return true;
            for (int i = 0; i < inv.size(); i++) inv.set(i, before.get(i));
        }
        return false;
    }

    private static int craft(CommandSourceStack src, String spec) throws CommandSyntaxException {
        ServerPlayer player = src.getPlayerOrException();
        BlockBuddyEntity agent = requireBuddy(src, player);
        return agent == null ? 0 : craft(player, agent, spec);
    }

    public static int craft(ServerPlayer player, BlockBuddyEntity agent, String spec) {
        agent.hearCommand();
        String[] parts = arguments(spec);
        int wanted = count(parts, 1, false);
        if (wanted < 1) return invalid(agent, "Use craft <item> [positive count].");
        Item target = resolveItem(parts[0]);
        if (target == null) return invalid(agent, "Unknown or ambiguous item. Use a namespaced item ID.");
        int previous = countIn(agent.inv, target);
        boolean ok = craftRecursive(player, agent, target, wanted, 4);
        int made = countIn(agent.inv, target) - previous;
        agent.reportEvent("craft", BuiltInRegistries.ITEM.getKey(target).toString(), made, ok,
                ok ? "I have " + countIn(agent.inv, target) + " " + parts[0] + "."
                        : "I cannot craft " + parts[0] + ". I need ingredients or inventory space.");
        return ok ? 1 : 0;
    }

    private static int container(CommandSourceStack src, String spec, boolean take)
            throws CommandSyntaxException {
        ServerPlayer player = src.getPlayerOrException();
        BlockBuddyEntity agent = requireBuddy(src, player);
        return agent == null ? 0 : container(player, agent, spec, take);
    }

    public static int container(ServerPlayer player, BlockBuddyEntity agent, String spec, boolean take) {
        agent.hearCommand();
        String[] parts = arguments(spec);
        int count = count(parts, 64, true);
        if (count < 1) return invalid(agent, "Use take/store <item> [positive count or all].");
        String selector = parts[0];
        Item item = resolveItem(selector);
        if (item == null) return invalid(agent, "Unknown or ambiguous item. Use a namespaced item ID.");
        int moved = 0;
        BlockPos center = agent.blockPosition();
        for (BlockPos pos : BlockPos.betweenClosed(center.offset(-12, -6, -12),
                                                   center.offset(12, 6, 12))) {
            if (moved >= count) break;
            BlockEntity be = player.serverLevel().getBlockEntity(pos);
            if (be == null) continue;
            IItemHandler handler = player.serverLevel().getCapability(
                    Capabilities.ItemHandler.BLOCK, pos, player.serverLevel().getBlockState(pos), be, null);
            if (handler == null) continue;
            if (take) {
                for (int slot = 0; slot < handler.getSlots() && moved < count; slot++) {
                    ItemStack in = handler.getStackInSlot(slot);
                    if (in.isEmpty()) continue;
                    if (in.getItem() != item) continue;
                    int amount = Math.min(count - moved, inventoryRoom(agent, in));
                    if (amount == 0) continue;
                    ItemStack pulled = handler.extractItem(slot, amount, false);
                    moved += insertIntoAgent(agent, pulled);
                }
            } else {
                NonNullList<ItemStack> inv = agent.inv;
                for (ItemStack stack : inv) {
                    if (moved >= count || stack.isEmpty()) continue;
                    if (stack.getItem() != item) continue;
                    int offer = Math.min(count - moved, stack.getCount());
                    ItemStack leftover = ItemHandlerHelper.insertItem(
                            handler, stack.copyWithCount(offer), false);
                    int accepted = offer - leftover.getCount();
                    stack.shrink(accepted);
                    moved += accepted;
                }
            }
        }
        agent.reportEvent(take ? "take" : "store", BuiltInRegistries.ITEM.getKey(item).toString(), moved, moved > 0,
                (take ? "I took " : "I stored ") + moved + " " + selector + ".");
        return moved > 0 ? 1 : 0;
    }

    private static int state(CommandSourceStack src) throws CommandSyntaxException {
        ServerPlayer player = src.getPlayerOrException();
        return state(player, nearest(player));
    }

    public static int state(ServerPlayer player, BlockBuddyEntity agent) {
        if (agent != null) agent.hearCommand();
        player.sendSystemMessage(Component.literal("[agentstate] " + stateJson(agent)));
        return agent == null ? 0 : 1;
    }

    public static JsonObject stateJson(BlockBuddyEntity agent) {
        JsonObject state = new JsonObject();
        if (agent == null) {
            state.add("agent", com.google.gson.JsonNull.INSTANCE);
            return state;
        }
        state.addProperty("name", agent.getBuddyName());
        JsonArray pos = new JsonArray();
        pos.add(agent.getX());
        pos.add(agent.getY());
        pos.add(agent.getZ());
        state.add("pos", pos);
        state.addProperty("health", agent.getHealth());
        JsonObject inventory = new JsonObject();
        for (ItemStack stack : agent.inv) {
            if (stack.isEmpty()) continue;
            ResourceLocation key = BuiltInRegistries.ITEM.getKey(stack.getItem());
            String name = key.toString();
            int previous = inventory.has(name) ? inventory.get(name).getAsInt() : 0;
            inventory.addProperty(name, previous + stack.getCount());
        }
        state.add("inventory", inventory);
        state.addProperty("mining", agent.isMining());
        state.addProperty("following", agent.isFollowing());
        return state;
    }


    private static int spawnNamed(CommandSourceStack src, String want, boolean auto)
            throws CommandSyntaxException {
        ServerPlayer player = src.getPlayerOrException();
        List<BlockBuddyEntity> existing = allBuddies(player);
        java.util.Set<String> taken = new java.util.HashSet<>();
        for (BlockBuddyEntity b : existing) taken.add(b.getBuddyName());
        if (existing.size() >= MAX_BUDDIES) {
            src.sendFailure(Component.literal("[blockbuddy] 16 companions already out — no more room!"));
            return 0;
        }
        String base = BuddyConfig.companionName.get();
        if (base == null || base.isBlank()) base = "Michael";
        String name = want;
        if (auto) {
            // ponytail: linear scan for first free Michael/MichaelN; N is tiny (<=16)
            name = base;
            int n = 2;
            while (taken.contains(name)) name = base + n++;
        } else if (taken.contains(want)) {
            int n = 2;
            name = want + n;
            while (taken.contains(name)) name = want + (++n);
        }
        BlockBuddyEntity agent = new BlockBuddyEntity(
                BlockBuddy.BUDDY.get(), player.serverLevel());
        agent.setBuddyName(name);
        agent.setOwner(player);
        agent.setCustomName(Component.literal(name));
        agent.setCustomNameVisible(true);
        agent.setPos(player.getX() + 1.0, player.getY(), player.getZ());
        agent.setPersistenceRequired();
        player.serverLevel().addFreshEntity(agent);
        agent.hearCommand();
        agent.reportEvent("spawn", "", 0, true,
                "Hey " + player.getGameProfile().getName() + "! Ready to help.");
        return 1;
    }
    /**
     * Re-bind every orphaned companion: the one nearest the caller becomes
     * theirs and follows; any extras hold position so only one moves with you.
     * Fixes the stuck-twin glitch after a shim restart without needing spawn.
     */
    private static int reconnect(CommandSourceStack src) throws CommandSyntaxException {
        ServerPlayer player = src.getPlayerOrException();
        List<BlockBuddyEntity> buddies = allBuddies(player);
        if (buddies.isEmpty()) {
            src.sendFailure(Component.literal("[blockbuddy] no companions in this world — /blockbuddy spawn first"));
            return 0;
        }
        buddies.sort(Comparator.comparingDouble(e -> e.distanceToSqr(player)));
        BlockBuddyEntity mine = buddies.get(0);
        int parked = 0;
        for (BlockBuddyEntity b : buddies) {
            if (b == mine) continue;
            b.hearCommand();
            b.stopAll();
            parked++;
        }
        mine.hearCommand();
        mine.setOwner(player);
        mine.reportEvent("reconnect", "", parked, true,
                "I found you again. I will follow you.");
        return 1;
    }
    private static int run(CommandSourceStack src, String cmd) throws CommandSyntaxException {
        ServerPlayer player = src.getPlayerOrException();
        BlockBuddyEntity agent = requireBuddy(src, player);
        if (agent == null) return 0;
        agent.hearCommand();
        String[] parts = arguments(cmd);
        if (parts.length == 0) return invalid(agent, "Use goto <x> <y> <z>, follow, or stop.");
        switch (parts[0]) {
            case "stop" -> agent.stopAll();
            case "follow" -> agent.setFollow(player);
            case "goto" -> {
                if (parts.length == 4) {
                    try {
                        agent.setGoto(new BlockPos(Integer.parseInt(parts[1]),
                                Integer.parseInt(parts[2]), Integer.parseInt(parts[3])));
                    } catch (NumberFormatException e) {
                        return invalid(agent, "Coordinates must be whole numbers within the integer range.");
                    }
                } else if (parts.length == 1) {
                    agent.walkOwnerLook(player);
                } else {
                    return invalid(agent, "Use goto <x> <y> <z>, or goto to follow your view.");
                }
            }
            default -> {
                return invalid(agent, "Unknown run command. Use goto, follow, or stop.");
            }
        }
        agent.reportEvent(parts[0], "", 0, true, switch (parts[0]) {
            case "stop" -> "Stopping.";
            case "follow" -> "I will follow you.";
            default -> "I will walk there.";
        });
        return 1;
    }

    private static int brain(CommandSourceStack src, boolean on) throws CommandSyntaxException {
        ServerPlayer player = src.getPlayerOrException();
        BlockBuddyEntity agent = requireBuddy(src, player);
        if (agent == null) return 0;
        agent.hearCommand();
        agent.setBrainVoice(on);
        return 1;
    }

    /** Audio runs only in the optional Python process on its local host. */
    private static int voice(CommandSourceStack src) {
        src.sendSuccess(() -> Component.literal(
                "[blockbuddy] Java does not download or play voice audio. "
                + "For optional local Python playback, set BLOCKBUDDY_TTS=1 in the Python .env. "
                + "Set BLOCKBUDDY_KOKORO_MODEL and BLOCKBUDDY_KOKORO_VOICES to your existing local files. "
                + "Install kokoro-onnx and sounddevice in that Python environment. "
                + "Only the Python host plays audio. Minecraft clients do not receive it."), false);
        return 1;
    }
}
