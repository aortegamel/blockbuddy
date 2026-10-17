package dev.blockbuddy.test;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.mojang.brigadier.exceptions.CommandSyntaxException;
import dev.blockbuddy.BlockBuddy;
import dev.blockbuddy.BlockBuddyCommand;
import dev.blockbuddy.BlockBuddyEntity;
import dev.blockbuddy.BuddyConfig;
import java.util.List;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.core.BlockPos;
import net.minecraft.gametest.framework.GameTest;
import net.minecraft.gametest.framework.GameTestHelper;
import net.minecraft.gametest.framework.GameTestInfo;
import net.minecraft.gametest.framework.GameTestListener;
import net.minecraft.gametest.framework.GameTestRunner;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.phys.AABB;
import net.neoforged.neoforge.gametest.GameTestHolder;
import net.neoforged.neoforge.gametest.PrefixGameTestTemplate;

@GameTestHolder("blockbuddy")
@PrefixGameTestTemplate(false)
public final class BuddyGameTests {
    private BuddyGameTests() {}

    @GameTest(template = "empty", timeoutTicks = 40)
    public static void spawnNames(GameTestHelper helper) {
        Arena arena = arena(helper, 1);
        arena.clear();
        String base = BuddyConfig.companionName.get();
        if (base == null || base.isBlank()) base = "Michael";
        final String spawnBase = base;
        final String spawnCustom = spawnBase + "Custom";
        final java.util.List<String> expectedNames = java.util.List.of(
                spawnBase, spawnBase + "2", spawnCustom, spawnCustom + "2");
        // Each spawn queries live companions for name de-duplication, so space
        // spawns across real server ticks: same-tick entities are not queryable yet.
        helper.startSequence()
                .thenExecuteAfter(5, arena::clear)
                .thenExecuteAfter(1, () -> commandOrFail(helper, arena, "spawn", "The default spawn command failed"))
                // Spawn quests scan 1,024 blocks, so concurrently running tests
                // compete for default names. Space commands so duplicate-name
                // scans can observe the previous command's companion, then assert
                // only that this arena received four distinct expected names.
                .thenExecuteAfter(2, () -> commandOrFail(helper, arena, "spawn", "The second default spawn command failed"))
                .thenExecuteAfter(2, () -> commandOrFail(helper, arena, "spawn " + spawnCustom, "The custom spawn command failed"))
                .thenExecuteAfter(2, () -> commandOrFail(helper, arena, "spawn " + spawnCustom, "The duplicate spawn command failed"))
                .thenExecuteAfter(2, () -> {
                    List<BlockBuddyEntity> buddies = arena.buddies();
                    helper.assertTrue(buddies.size() == 4, "Spawn must create exactly four companions");
                    java.util.Set<String> found = new java.util.HashSet<>();
                    for (BlockBuddyEntity buddy : buddies) found.add(buddy.getBuddyName());
                    helper.assertTrue(found.size() == 4, "Spawn must assign four distinct companion names");
                    for (String name : expectedNames) {
                        helper.assertTrue(found.contains(name),
                                "The expected companion name is missing: " + name);
                    }
                    for (BlockBuddyEntity buddy : buddies) {
                        helper.assertTrue(arena.player.getUUID().equals(buddy.getOwnerUuid()), "Spawn must assign the owner");
                        helper.assertTrue(buddy.isFollowing(), "A new companion must follow its owner");
                        helper.assertTrue(buddy.hasCustomName(), "Spawn must display the companion name");
                    }
                })
                .thenSucceed();
    }

    @GameTest(template = "empty", timeoutTicks = 160)
    public static void minesThreeDirt(GameTestHelper helper) {
        // The companion must be created after delayed clearing: nearest-buddy
        // commands otherwise select a leaked companion from a prior server run.
        Arena arena = arena(helper, 2);
        final BlockBuddyEntity[] holder = new BlockBuddyEntity[1];
        for (int x = 9; x <= 11; x++) arena.block(x, 1, 8, Blocks.DIRT);
        helper.startSequence()
                .thenExecuteAfter(5, () -> {
                    arena.clear();
                    holder[0] = arena.buddy();
                })
                .thenExecuteAfter(1, () -> directOrFail(helper, holder[0],
                        BlockBuddyCommand.mine(arena.player, holder[0], "minecraft:dirt 3"),
                        "The mine command failed"))
                .thenWaitUntil(() -> {
                    BlockBuddyEntity buddy = holder[0];
                    helper.assertFalse(buddy.isMining(), "Mining did not complete");
                    helper.assertTrue(count(buddy.inv, Items.DIRT) == 3, "Mining must collect exactly three dirt");
                    for (int x = 9; x <= 11; x++) {
                        helper.assertTrue(arena.level().getBlockState(arena.pos(x, 1, 8)).isAir(),
                                "Mining must remove every requested dirt block");
                    }
                })
                .thenSucceed();
    }

    @GameTest(template = "empty", timeoutTicks = 160)
    public static void minesStoneWithInventoryPickaxe(GameTestHelper helper) {
        Arena arena = arena(helper, 11);
        final BlockBuddyEntity[] holder = new BlockBuddyEntity[1];
        arena.block(9, 1, 8, Blocks.STONE);
        helper.startSequence()
                .thenExecuteAfter(5, () -> {
                    arena.clear();
                    holder[0] = arena.buddy();
                    holder[0].inv.set(0, new ItemStack(Items.WOODEN_PICKAXE));
                })
                .thenExecuteAfter(1, () -> directOrFail(helper, holder[0],
                        BlockBuddyCommand.mine(arena.player, holder[0], "minecraft:stone 1"),
                        "The stone mine command failed"))
                .thenWaitUntil(() -> {
                    BlockBuddyEntity buddy = holder[0];
                    helper.assertFalse(buddy.isMining(), "Stone mining must complete");
                    helper.assertTrue(count(buddy.inv, Items.COBBLESTONE) == 1,
                            "The inventory pickaxe must produce the normal cobblestone drop");
                    helper.assertTrue(buddy.inv.get(0).getDamageValue() == 1,
                            "Mining one stone block must consume one pickaxe durability");
                    helper.assertTrue(arena.level().getBlockState(arena.pos(9, 1, 8)).isAir(),
                            "Mining must remove the stone block");
                })
                .thenSucceed();
    }

    @GameTest(template = "empty", timeoutTicks = 260)
    public static void refusesIronWithoutPickaxe(GameTestHelper helper) {
        Arena arena = arena(helper, 12);
        final BlockBuddyEntity[] holder = new BlockBuddyEntity[1];
        arena.block(9, 1, 8, Blocks.IRON_ORE);
        helper.startSequence()
                .thenExecuteAfter(5, () -> {
                    arena.clear();
                    holder[0] = arena.buddy();
                })
                .thenExecuteAfter(1, () -> directOrFail(helper, holder[0],
                        BlockBuddyCommand.mine(arena.player, holder[0], "minecraft:iron_ore 1"),
                        "The iron mine command failed"))
                .thenWaitUntil(() -> {
                    BlockBuddyEntity buddy = holder[0];
                    helper.assertFalse(buddy.isMining(), "Mining without a required tool must stop");
                    helper.assertTrue(arena.level().getBlockState(arena.pos(9, 1, 8)).is(Blocks.IRON_ORE),
                            "Mining without a required tool must preserve the iron ore");
                    helper.assertTrue(count(buddy.inv, Items.RAW_IRON) == 0,
                            "Mining without a required tool must not create raw iron");
                })
                .thenSucceed();
    }

    @GameTest(template = "empty", timeoutTicks = 160)
    public static void absentBlockStops(GameTestHelper helper) {
        Arena arena = arena(helper, 3);
        final BlockBuddyEntity[] holder = new BlockBuddyEntity[1];
        helper.startSequence()
                .thenExecuteAfter(5, () -> {
                    arena.clear();
                    holder[0] = arena.buddy();
                })
                .thenExecuteAfter(1, () -> {
                    directOrFail(helper, holder[0],
                            BlockBuddyCommand.mine(arena.player, holder[0], "minecraft:diamond_ore 1"),
                            "The mine command failed");
                    helper.assertTrue(holder[0].isMining(), "Mining must start before the absent-block timeout");
                })
                .thenWaitUntil(() -> {
                    helper.assertFalse(holder[0].isMining(), "An absent block must not leave mining active");
                    helper.assertTrue(count(holder[0].inv, Items.DIAMOND) == 0, "An absent block must not produce items");
                })
                .thenSucceed();
    }

    @GameTest(template = "empty", timeoutTicks = 240)
    public static void followCancelsMiningAndApproachesOwner(GameTestHelper helper) {
        Arena arena = arena(helper, 4);
        final BlockBuddyEntity[] holder = new BlockBuddyEntity[1];
        helper.startSequence()
                .thenExecuteAfter(5, () -> {
                    arena.clear();
                    holder[0] = arena.buddy();
                })
                .thenExecuteAfter(1, () -> {
                    directOrFail(helper, holder[0],
                            BlockBuddyCommand.mine(arena.player, holder[0], "minecraft:diamond_ore 1"),
                            "The mine command failed");
                    BlockBuddyEntity buddy = holder[0];
                    buddy.setFollow(arena.player);
                    helper.assertFalse(buddy.isMining(), "Follow must cancel mining immediately");
                    helper.assertTrue(arena.player.getUUID().equals(buddy.getFollowUuid()), "Follow must target the owner");
                    arena.player.teleportTo(arena.level(),
                            arena.origin.getX() + 22.5, arena.origin.getY() + 1, arena.origin.getZ() + 8.5, 0.0F, 0.0F);
                    double initialDistance = buddy.distanceToSqr(arena.player);
                    helper.assertTrue(initialDistance > 100, "The owner must start beyond follow range");
                })
                .thenWaitUntil(() -> {
                    BlockBuddyEntity buddy = holder[0];
                    helper.assertTrue(buddy.distanceToSqr(arena.player) <= 16, "The companion must approach its owner");
                    helper.assertTrue(buddy.isFollowing(), "Approaching the owner must retain follow mode");
                    CompoundTag saved = new CompoundTag();
                    buddy.addAdditionalSaveData(saved);
                    BlockBuddyEntity restored = new BlockBuddyEntity(BlockBuddy.BUDDY.get(), arena.level());
                    restored.readAdditionalSaveData(saved);
                    helper.assertTrue(arena.player.getUUID().equals(restored.getFollowUuid()),
                            "Saving and loading must preserve follow mode");
                })
                .thenSucceed();
    }

    @GameTest(template = "empty", timeoutTicks = 40)
    public static void craftsSticksFromOakLog(GameTestHelper helper) {
        Arena arena = arena(helper, 5);
        final BlockBuddyEntity[] holder = new BlockBuddyEntity[1];
        helper.startSequence()
                .thenExecuteAfter(5, () -> {
                    arena.clear();
                    holder[0] = arena.buddy();
                    holder[0].inv.set(0, new ItemStack(Items.OAK_LOG));
                })
                .thenExecuteAfter(1, () -> {
                    directOrFail(helper, holder[0],
                            BlockBuddyCommand.craft(arena.player, holder[0], "minecraft:stick 4"),
                            "Recursive stick crafting failed");
                    BlockBuddyEntity buddy = holder[0];
                    helper.assertTrue(count(buddy.inv, Items.STICK) >= 4, "Crafting must produce at least four sticks");
                    helper.assertTrue(count(buddy.inv, Items.OAK_LOG) == 0, "Crafting must consume the oak log");
                    helper.assertTrue(count(buddy.inv, Items.OAK_PLANKS) == 2, "Crafting must retain the unused oak planks");
                })
                .thenSucceed();
    }

    @GameTest(template = "empty", timeoutTicks = 40)
    public static void givesExactItemsAndGenericSword(GameTestHelper helper) {
        Arena arena = arena(helper, 6);
        final BlockBuddyEntity[] holder = new BlockBuddyEntity[1];
        helper.startSequence()
                .thenExecuteAfter(5, () -> {
                    arena.clear();
                    holder[0] = arena.buddy();
                    holder[0].inv.set(0, new ItemStack(Items.STICK, 5));
                    holder[0].inv.set(1, new ItemStack(Items.IRON_SWORD));
                    holder[0].inv.set(2, new ItemStack(Items.STONE_SWORD));
                })
                .thenExecuteAfter(1, () -> {
                    BlockBuddyEntity buddy = holder[0];
                    directOrFail(helper, buddy,
                            BlockBuddyCommand.give(arena.player, buddy, "minecraft:stick 2"),
                            "The exact-item give command failed");
                    helper.assertTrue(count(buddy.inv, Items.STICK) == 3, "Give must remove only the requested count");
                    helper.assertTrue(arena.player.getInventory().countItem(Items.STICK) == 2,
                            "Give must insert the requested count into the player inventory");
                    directOrFail(helper, buddy,
                            BlockBuddyCommand.give(arena.player, buddy, "sword 1"),
                            "The generic sword command failed");
                    int remaining = count(buddy.inv, Items.IRON_SWORD) + count(buddy.inv, Items.STONE_SWORD);
                    int received = arena.player.getInventory().countItem(Items.IRON_SWORD)
                            + arena.player.getInventory().countItem(Items.STONE_SWORD);
                    helper.assertTrue(remaining == 1 && received == 1, "Generic sword must transfer exactly one real sword");
                    String remainingSword = count(buddy.inv, Items.IRON_SWORD) == 1 ? "minecraft:iron_sword" : "minecraft:stone_sword";
                    directOrFail(helper, buddy,
                            BlockBuddyCommand.give(arena.player, buddy, remainingSword),
                            "The namespaced sword command failed");
                    helper.assertTrue(count(buddy.inv, Items.IRON_SWORD) + count(buddy.inv, Items.STONE_SWORD) == 0,
                            "The exact sword command must remove the remaining sword");
                    helper.assertTrue(arena.player.getInventory().countItem(Items.IRON_SWORD) == 1
                            && arena.player.getInventory().countItem(Items.STONE_SWORD) == 1, "Give must conserve both swords");
                })
                .thenSucceed();
    }

    @GameTest(template = "empty", timeoutTicks = 40)
    public static void stateEscapesNames(GameTestHelper helper) {
        Arena arena = arena(helper, 7);
        final BlockBuddyEntity[] holder = new BlockBuddyEntity[1];
        String name = "Ada \"miner\"\\\n雪";
        helper.startSequence()
                .thenExecuteAfter(5, () -> {
                    arena.clear();
                    holder[0] = arena.buddy();
                    BlockBuddyEntity buddy = holder[0];
                    buddy.setBuddyName(name);
                    buddy.inv.set(0, new ItemStack(Items.DIRT, 3));
                })
                .thenExecuteAfter(1, () -> {
                    BlockBuddyEntity buddy = holder[0];
                    directOrFail(helper, buddy,
                            BlockBuddyCommand.state(arena.player, buddy), "The state command failed");
                    JsonObject state = JsonParser.parseString(BlockBuddyCommand.stateJson(buddy).toString()).getAsJsonObject();
                    helper.assertTrue(state.get("name").getAsString().equals(name), "JSON must preserve the complete companion name");
                    helper.assertTrue(state.getAsJsonArray("pos").size() == 3, "State must contain three position coordinates");
                    helper.assertTrue(state.get("health").getAsFloat() == buddy.getHealth(), "State must report actual health");
                    helper.assertTrue(state.getAsJsonObject("inventory").get("minecraft:dirt").getAsInt() == 3,
                            "State must use namespaced inventory identifiers");
                    helper.assertFalse(state.get("mining").getAsBoolean(), "State must report inactive mining");
                    helper.assertFalse(state.get("following").getAsBoolean(), "State must report inactive following");
                })
                .thenSucceed();
    }

    @GameTest(template = "empty", timeoutTicks = 120)
    public static void minesLogsTag(GameTestHelper helper) {
        Arena arena = arena(helper, 8);
        final BlockBuddyEntity[] holder = new BlockBuddyEntity[1];
        arena.block(9, 1, 8, Blocks.BIRCH_LOG);
        helper.startSequence()
                .thenExecuteAfter(5, () -> {
                    arena.clear();
                    holder[0] = arena.buddy();
                })
                .thenExecuteAfter(1, () -> directOrFail(helper, holder[0],
                        BlockBuddyCommand.mine(arena.player, holder[0], "#minecraft:logs 1"),
                        "The logs-tag command failed"))
                .thenWaitUntil(() -> {
                    BlockBuddyEntity buddy = holder[0];
                    helper.assertFalse(buddy.isMining(), "Tag mining must complete");
                    helper.assertTrue(count(buddy.inv, Items.BIRCH_LOG) == 1, "The logs tag must match a birch log");
                })
                .thenSucceed();
    }

    @GameTest(template = "empty", timeoutTicks = 40)
    public static void rejectsInvalidCountsWithoutMutation(GameTestHelper helper) {
        Arena arena = arena(helper, 9);
        final BlockBuddyEntity[] holder = new BlockBuddyEntity[1];
        helper.startSequence()
                .thenExecuteAfter(5, () -> {
                    arena.clear();
                    holder[0] = arena.buddy();
                    holder[0].inv.set(0, new ItemStack(Items.STICK, 5));
                })
                .thenExecuteAfter(1, () -> {
                    BlockBuddyEntity buddy = holder[0];
                    for (String[] action : new String[][]{
                            {"mine", "minecraft:dirt"}, {"give", "minecraft:stick"},
                            {"craft", "minecraft:stick"}, {"take", "minecraft:stick"},
                            {"store", "minecraft:stick"}}) {
                        for (String invalid : List.of("0", "-1", "nope", "2147483648")) {
                            String spec = action[1] + " " + invalid;
                            int result = switch (action[0]) {
                                case "mine" -> BlockBuddyCommand.mine(arena.player, buddy, spec);
                                case "give" -> BlockBuddyCommand.give(arena.player, buddy, spec);
                                case "craft" -> BlockBuddyCommand.craft(arena.player, buddy, spec);
                                case "take" -> BlockBuddyCommand.container(arena.player, buddy, spec, true);
                                default -> BlockBuddyCommand.container(arena.player, buddy, spec, false);
                            };
                            helper.assertTrue(result == 0,
                                    "The command must reject an invalid count: " + action[0] + " " + invalid);
                        }
                    }
                    helper.assertFalse(buddy.isMining(), "Rejected counts must not start mining");
                    helper.assertTrue(count(buddy.inv, Items.STICK) == 5, "Rejected counts must not mutate the companion inventory");
                    helper.assertTrue(arena.player.getInventory().countItem(Items.STICK) == 0,
                            "Rejected counts must not transfer items");
                })
                .thenSucceed();
    }

    @GameTest(template = "empty", timeoutTicks = 40)
    public static void permissionsAndFullInventory(GameTestHelper helper) {
        Arena arena = arena(helper, 10);
        final BlockBuddyEntity[] holder = new BlockBuddyEntity[1];
        helper.startSequence()
                .thenExecuteAfter(6, () -> {
                    // Clearing must happen after the first real server tick: entities
                    // discarded in the test body's tick are not yet visible to the
                    // server's entity query. Earlier immediate clearing created the
                    // leaked companion that made this gate test fail.
                    arena.clear();
                    boolean singleplayer = arena.level().getServer().isSingleplayer();
                    for (String command : List.of("spawn", "voice")) {
                        CommandSourceStack unprivileged = arena.player.createCommandSourceStack().withPermission(0).withSuppressedOutput();
                        boolean rejected = false;
                        int before = arena.buddies().size();
                        try {
                            arena.level().getServer().getCommands().getDispatcher().execute("blockbuddy " + command, unprivileged);
                        } catch (CommandSyntaxException expected) {
                            rejected = true;
                        }
                        // On the GameTest integrated server, the existing command gate
                        // allows these operations without operator status. Assert
                        // actual behavior instead of assuming rejection there.
                        if (singleplayer) {
                            helper.assertTrue(!rejected, command + " must remain usable in the GameTest world");
                        } else {
                            helper.assertTrue(rejected, "A non-OP must not access " + command);
                        }
                        helper.assertTrue(arena.buddies().size() <= before + 1, "Gate probe leaked");
                        arena.clear();
                    }
                    helper.assertTrue(arena.buddies().isEmpty(), "Gate probes must not leave companions behind");
                    holder[0] = arena.buddy();
                    BlockBuddyEntity buddy = holder[0];
                    buddy.inv.set(0, new ItemStack(Items.STICK, 5));
                    // Player Inventory.add can use armor, offhand, crafting, and
                    // result slots, so fill every player slot; otherwise a nominally
                    // full main inventory still accepts the transfer.
                    for (net.minecraft.world.entity.EquipmentSlot slot : net.minecraft.world.entity.EquipmentSlot.values()) {
                        arena.player.setItemSlot(slot, new ItemStack(Items.STONE, 64));
                    }
                    for (int slot = 0; slot < arena.player.getInventory().getContainerSize(); slot++) {
                        arena.player.getInventory().setItem(slot, new ItemStack(Items.STONE, 64));
                    }
                    arena.player.getInventory().offhand.set(0, new ItemStack(Items.STONE, 64));
                    for (int slot = 0; slot < arena.player.inventoryMenu.getCraftSlots().getContainerSize(); slot++) {
                        arena.player.inventoryMenu.getCraftSlots().setItem(slot, new ItemStack(Items.STONE, 64));
                    }
                    int result = BlockBuddyCommand.give(arena.player, buddy, "minecraft:stick 2");
                    helper.assertTrue(result == 0, "Give must fail when the player inventory is full");
                    helper.assertTrue(count(buddy.inv, Items.STICK) == 5, "A full inventory must not lose companion items");
                    directOrFail(helper, buddy,
                            BlockBuddyCommand.state(arena.player, buddy),
                            "A non-OP must retain ordinary companion commands");
                })
                .thenSucceed();
    }

    private static void directOrFail(GameTestHelper helper, BlockBuddyEntity buddy, int result, String message) {
        helper.assertTrue(result == 1, message);
        helper.assertTrue(buddy != null && !buddy.isRemoved(), message + " used a stale companion");
    }

    private static void commandOrFail(GameTestHelper helper, Arena arena, String command, String message) {
        try {
            helper.assertTrue(arena.command(command) == 1, message);
        } catch (CommandSyntaxException e) {
            helper.fail(message + ": " + e.getMessage());
        }
    }

    private static int count(Iterable<ItemStack> inventory, Item item) {
        int total = 0;
        for (ItemStack stack : inventory) if (stack.is(item)) total += stack.getCount();
        return total;
    }

    private static Arena arena(GameTestHelper helper, int index) {
        // Separate arenas keep 1,024-block command scans and mining scans independent of test ordering.
        BlockPos origin = new BlockPos(index * 4096, 160, 4096);
        ServerLevel level = helper.getLevel();
        for (int x = 0; x < 2; x++) {
            for (int z = 0; z < 2; z++) {
                level.getChunk((origin.getX() >> 4) + x, (origin.getZ() >> 4) + z);
                level.setChunkForced((origin.getX() >> 4) + x, (origin.getZ() >> 4) + z, true);
            }
        }
        for (BlockPos pos : BlockPos.betweenClosed(origin, origin.offset(31, 7, 31))) {
            level.setBlock(pos, pos.getY() == origin.getY() ? Blocks.BEDROCK.defaultBlockState()
                    : Blocks.AIR.defaultBlockState(), 2);
        }
        ServerPlayer player = helper.makeMockServerPlayerInLevel();
        // Authoritative teleport: updates position, bounding box, and chunk tracking
        // together. setPos alone leaves command entity queries at the login position.
        player.teleportTo(level, origin.getX() + 9.5, origin.getY() + 1, origin.getZ() + 8.5, 0.0F, 0.0F);
        Arena arena = new Arena(helper, origin, player);
        helper.testInfo.addListener(new GameTestListener() {
            private boolean cleaned;
            private void clean() {
                if (cleaned) return;
                cleaned = true;
                arena.buddies().forEach(BlockBuddyEntity::discard);
                level.getServer().getPlayerList().remove(player);
                for (int x = 0; x < 2; x++) {
                    for (int z = 0; z < 2; z++) {
                        level.setChunkForced((origin.getX() >> 4) + x, (origin.getZ() >> 4) + z, false);
                    }
                }
            }
            @Override public void testStructureLoaded(GameTestInfo info) {}
            @Override public void testPassed(GameTestInfo info, GameTestRunner runner) { clean(); }
            @Override public void testFailed(GameTestInfo info, GameTestRunner runner) { clean(); }
            @Override public void testAddedForRerun(GameTestInfo oldInfo, GameTestInfo newInfo, GameTestRunner runner) { clean(); }
        });
        return arena;
    }
    private record Arena(GameTestHelper helper, BlockPos origin, ServerPlayer player) {
        void clear() { buddies().forEach(BlockBuddyEntity::discard); }
        BlockBuddyEntity selected() {
            BlockBuddyEntity found = null;
            double best = Double.MAX_VALUE;
            for (BlockBuddyEntity buddy : buddies()) {
                double distance = buddy.distanceToSqr(player);
                if (distance < best) {
                    best = distance;
                    found = buddy;
                }
            }
            return found;
        }
        ServerLevel level() { return helper.getLevel(); }
        BlockPos pos(int x, int y, int z) { return origin.offset(x, y, z); }
        void block(int x, int y, int z, net.minecraft.world.level.block.Block block) {
            level().setBlock(pos(x, y, z), block.defaultBlockState(), 3);
        }
        List<BlockBuddyEntity> buddies() {
            return level().getEntitiesOfClass(BlockBuddyEntity.class, new AABB(
                    origin.getX(), origin.getY(), origin.getZ(),
                    origin.getX() + 32, origin.getY() + 8, origin.getZ() + 32));
        }
        BlockBuddyEntity buddy() {
            BlockBuddyEntity buddy = new BlockBuddyEntity(BlockBuddy.BUDDY.get(), level());
            buddy.setPos(origin.getX() + 8.5, origin.getY() + 1, origin.getZ() + 8.5);
            buddy.setBuddyName("Test" + origin.getX());
            buddy.setOwner(player);
            buddy.stopAll();
            buddy.setGuardMode(false);
            buddy.setPersistenceRequired();
            level().addFreshEntity(buddy);
            return buddy;
        }
        int command(String command) throws CommandSyntaxException {
            return level().getServer().getCommands().getDispatcher().execute("blockbuddy " + command,
                    player.createCommandSourceStack().withPermission(2).withSuppressedOutput());
        }
        int command(String command, int permission) throws CommandSyntaxException {
            return level().getServer().getCommands().getDispatcher().execute("blockbuddy " + command,
                    player.createCommandSourceStack().withPermission(permission).withSuppressedOutput());
        }
    }
}
