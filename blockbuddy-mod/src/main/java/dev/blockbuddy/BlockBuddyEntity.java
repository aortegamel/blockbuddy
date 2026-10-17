package dev.blockbuddy;

import java.util.List;
import java.util.UUID;

import net.minecraft.core.BlockPos;
import net.minecraft.core.NonNullList;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.ContainerHelper;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.entity.Mob;
import net.minecraft.world.entity.PathfinderMob;
import net.minecraft.world.entity.ai.attributes.AttributeSupplier;
import net.minecraft.world.entity.ai.attributes.Attributes;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.entity.BlockEntity;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.tags.BlockTags;
import net.minecraft.world.entity.monster.Monster;
import net.minecraft.world.level.block.Blocks;

/**
 * Built-in companion body: a server-side mob with its own inventory,
 * vanilla pathfinding and vanilla block breaking. No external jars.
 */
public class BlockBuddyEntity extends PathfinderMob {

    /** Player-inventory-sized storage (36 main slots). */
    public final NonNullList<ItemStack> inv = NonNullList.withSize(36, ItemStack.EMPTY);

    private String buddyName = "Michael";
    private UUID followUuid;
    private BlockPos gotoTarget;
    private Block miningBlock;
    private int miningWant;
    private int miningMined;

    private UUID ownerUuid;
    private boolean guardMode = true;
    private long lastHeardTick;
    private long gearedTick = -1;
    private long askedTick;
    private long lastTauntTick = -200;

    public BlockBuddyEntity(EntityType<? extends PathfinderMob> type, Level level) {
        super(type, level);
    }

    /** Michael-voice: chat as the companion entity so the player sees <name> lines. */
    public void say(String text) {
        if (level() instanceof net.minecraft.server.level.ServerLevel server) {
            server.getServer().getPlayerList().broadcastSystemMessage(
                    net.minecraft.network.chat.Component.literal("<" + buddyName + "> " + text), false);
        }
        speakLine(text);
    }

    /**
     * Spoken twin of say: best-effort Kokoro render plus a vanilla positional
     * cue at the buddy, so he is audible nearby. Range scales with the
     * proximityRange config; player-to-player spatial audio stays with Simple
     * Voice Chat. Never throws, never blocks chat; silent unless voice is
     * enabled and the model file is cached. No voicechat classes touched, so
     * nothing breaks when that mod is absent.
     */
    private void speakLine(String text) {
        final boolean enabled;
        final int range;
        final String voice;
        final java.nio.file.Path model;
        try {
            enabled = BuddyConfig.voiceEnabled.get();
            range = BuddyConfig.proximityRange.get();
            voice = BuddyConfig.voiceName.get();
            model = BuddyConfig.modelFile();
        } catch (Throwable ignored) {
            return; // config shape missing — text only
        }
        if (!enabled || text == null || text.isEmpty()) return;
        if (model == null || !java.nio.file.Files.isRegularFile(model)) return;
        if (!(level() instanceof net.minecraft.server.level.ServerLevel server)) return;
        final BlockPos pos = blockPosition();
        final float volume = Math.max(0.25F, Math.min(4.0F, range / 16.0F));
        final String line = text.length() > 280 ? text.substring(0, 280) : text;
        Thread voiceThread = new Thread(() -> {
            try {
                if (!renderVoice(voice, line)) return;
                server.getServer().execute(() -> {
                    try {
                        server.playSound(null, pos,
                                net.minecraft.sounds.SoundEvents.VILLAGER_AMBIENT,
                                net.minecraft.sounds.SoundSource.VOICE, volume, 1.0F);
                    } catch (Throwable ignored) {
                        // client already gone — chat line already went out
                    }
                });
            } catch (Throwable ignored) {
                // synthesis failed — chat line already went out
            }
        }, "blockbuddy-voice");
        voiceThread.setDaemon(true);
        voiceThread.start();
    }

    // ponytail: best-effort wav render only. Vanilla has no per-line voice
    // playback, so the audible cue stays a positional sound; upgrade to the
    // voicechat API or a client-decoder packet when per-line audio matters.
    private static boolean renderVoice(String voice, String text) {
        final java.nio.file.Path out;
        try {
            out = java.nio.file.Files.createTempFile("blockbuddy-voice", ".wav");
        } catch (Throwable ignored) {
            return false;
        }
        String[][] attempts = {
                {"kokoro-tts", "--voice", voice, "--out", out.toString(), text},
                {"python3", "-m", "kokoro", "--voice", voice, "--out", out.toString(), text},
        };
        for (String[] cmd : attempts) {
            try {
                Process proc = new ProcessBuilder(cmd).redirectErrorStream(true).start();
                // ponytail: fixed 60s cap; a hung TTS CLI must never wedge the voice thread
                if (proc.waitFor(60, java.util.concurrent.TimeUnit.SECONDS)
                        && proc.exitValue() == 0 && java.nio.file.Files.size(out) > 0) {
                    try {
                        java.nio.file.Files.deleteIfExists(out);
                    } catch (Throwable ignored) {
                    }
                    return true;
                }
                proc.destroyForcibly();
            } catch (Throwable ignored) {
                // CLI missing or failed — try the next one, else stay silent
            }
        }
        try {
            java.nio.file.Files.deleteIfExists(out);
        } catch (Throwable ignored) {
        }
        return false;
    }

    public static AttributeSupplier attributes() {
        return Mob.createMobAttributes()
                .add(Attributes.MAX_HEALTH, 20.0D)
                .add(Attributes.MOVEMENT_SPEED, 0.32D)
                .add(Attributes.FOLLOW_RANGE, 48.0D)
                .add(Attributes.ATTACK_DAMAGE, 3.0D)
                .build();
    }

    @Override
    public void addAdditionalSaveData(CompoundTag tag) {
        super.addAdditionalSaveData(tag);
        tag.putString("BuddyName", buddyName);
        if (ownerUuid != null) tag.putUUID("Owner", ownerUuid);
        tag.putBoolean("GuardMode", guardMode);
        tag.putLong("LastHeardTick", lastHeardTick);
        tag.putLong("GearedTick", gearedTick);
        tag.putLong("AskedTick", askedTick);
        ContainerHelper.saveAllItems(tag, inv, level().registryAccess());
    }

    @Override
    public void readAdditionalSaveData(CompoundTag tag) {
        super.readAdditionalSaveData(tag);
        buddyName = tag.getString("BuddyName");
        if (tag.hasUUID("Owner")) ownerUuid = tag.getUUID("Owner");
        guardMode = !tag.contains("GuardMode") || tag.getBoolean("GuardMode");
        lastHeardTick = tag.getLong("LastHeardTick");
        gearedTick = tag.contains("GearedTick") ? tag.getLong("GearedTick") : -1;
        askedTick = tag.getLong("AskedTick");
        ContainerHelper.loadAllItems(tag, inv, level().registryAccess());
    }

    /** Merge a stack into inv; returns the amount accepted. */
    public int insertStack(ItemStack stack) {
        int remaining = stack.getCount();
        for (ItemStack slot : inv) {
            if (remaining == 0) break;
            if (!slot.isEmpty() && ItemStack.isSameItemSameComponents(slot, stack)
                    && slot.getCount() < slot.getMaxStackSize()) {
                int moved = Math.min(remaining, slot.getMaxStackSize() - slot.getCount());
                slot.grow(moved);
                remaining -= moved;
            }
        }
        for (int i = 0; i < inv.size() && remaining > 0; i++) {
            if (inv.get(i).isEmpty()) {
                int moved = Math.min(remaining, stack.getMaxStackSize());
                inv.set(i, stack.copyWithCount(moved));
                remaining -= moved;
            }
        }
        return stack.getCount() - remaining;
    }

    public void setFollow(ServerPlayer player) {
        followUuid = player.getUUID();
        gotoTarget = null;
        if (!level().isClientSide) getNavigation().stop();
    }

    public void setGoto(BlockPos pos) {
        gotoTarget = pos.immutable();
        followUuid = null;
        if (!level().isClientSide) {
            getNavigation().moveTo(pos.getX() + 0.5D, pos.getY(), pos.getZ() + 0.5D, 1.0D);
        }
    }

    /** Look-at walk: head 12 blocks along the owner's view direction. */
    public void walkOwnerLook(ServerPlayer owner) {
        var look = owner.getLookAngle();
        setGoto(owner.blockPosition().offset(
                (int) Math.round(look.x * 12.0D), 0, (int) Math.round(look.z * 12.0D)));
    }

    public void startMining(Block block, int count) {
        miningBlock = block;
        miningWant = Math.max(1, count);
        miningMined = 0;
    }

    public boolean isMining() {
        return miningBlock != null && miningMined < miningWant;
    }

    public void stopAll() {
        followUuid = null;
        gotoTarget = null;
        miningBlock = null;
        if (!level().isClientSide) getNavigation().stop();
    }

    public void setOwner(ServerPlayer player) {
        setFollow(player);
        ownerUuid = player.getUUID();
        askedTick = tickCount;
        lastHeardTick = tickCount;
    }

    public UUID getOwnerUuid() {
        return ownerUuid;
    }

    public boolean isGuarding() {
        return guardMode;
    }

    public void setGuardMode(boolean guard) {
        guardMode = guard;
    }

    /** Slice 2 calls this whenever player chat names the buddy. */
    public void hearCommand() {
        lastHeardTick = tickCount;
    }

    public long getLastHeardTick() {
        return lastHeardTick;
    }

    public void setLastHeardTick(long tick) {
        lastHeardTick = tick;
    }

    public boolean needsGear() {
        return gearedTick < 0;
    }

    public void markGeared() {
        gearedTick = tickCount;
    }

    public long getGearedTick() {
        return gearedTick;
    }

    public long getAskedTick() {
        return askedTick;
    }

    public void setAskedTick(long tick) {
        askedTick = tick;
    }

    /** Next base block the self-gear chain still needs, or null when stocked. */
    public Block needsGearStage() {
        if (!hasItemContaining("log", "planks", "stick")) {
            Block log = nearestLog();
            return log != null ? log : Blocks.OAK_LOG;
        }
        if (!hasItemContaining("cobblestone")) return Blocks.COBBLESTONE;
        if (!hasItemContaining("raw_iron", "iron_ingot", "iron_ore")) return Blocks.IRON_ORE;
        return null;
    }

    private boolean hasItemContaining(String... needles) {
        for (ItemStack stack : inv) {
            if (stack.isEmpty()) continue;
            ResourceLocation key = BuiltInRegistries.ITEM.getKey(stack.getItem());
            if (key == null) continue;
            for (String needle : needles) {
                if (key.getPath().contains(needle)) return true;
            }
        }
        return false;
    }

    // ponytail: linear scan for any log type so gear-up works outside oak biomes; radius matches mineStep
    private Block nearestLog() {
        BlockPos center = blockPosition();
        BlockPos best = null;
        double bestDist = Double.MAX_VALUE;
        for (BlockPos pos : BlockPos.betweenClosed(
                center.offset(-12, -6, -12), center.offset(12, 6, 12))) {
            if (level().getBlockState(pos).is(BlockTags.LOGS)) {
                double d = pos.distSqr(center);
                if (d < bestDist) {
                    bestDist = d;
                    best = pos.immutable();
                }
            }
        }
        return best == null ? null : level().getBlockState(best).getBlock();
    }

    /** Attacks every tick; steers only when maySteer. Returns true when engaging. */
    private boolean guardTick(ServerLevel server, boolean maySteer) {
        if (!guardMode || ownerUuid == null) return false;
        if (!(server.getPlayerByUUID(ownerUuid) instanceof ServerPlayer owner)) return false;
        if (distanceToSqr(owner) > 256.0D) return false;
        List<Monster> hostiles =
                server.getEntitiesOfClass(Monster.class, getBoundingBox().inflate(8.0D));
        if (hostiles.isEmpty()) return false;
        Monster target = null;
        double bestDist = Double.MAX_VALUE;
        for (Monster hostile : hostiles) {
            if (hostile.getTarget() == owner) {
                double d = distanceToSqr(hostile);
                if (d < bestDist) {
                    bestDist = d;
                    target = hostile;
                }
            }
        }
        if (target == null) {
            for (Monster hostile : hostiles) {
                double d = distanceToSqr(hostile);
                if (d < bestDist) {
                    bestDist = d;
                    target = hostile;
                }
            }
        }
        if (target == null) return false;
        if (distanceToSqr(target) < 6.25D) doHurtTarget(target);
        if (!maySteer) return true;
        getNavigation().moveTo(target, 1.0D);
        if (tickCount - lastTauntTick >= 200) {
            lastTauntTick = tickCount;
            say("hey! leave " + owner.getGameProfile().getName() + " alone!");
        }
        return true;
    }

    private void gearTick() {
        if (!needsGear() || isMining()) return;
        // ponytail: entity tickCount restarts at 0 on reload; clamp saved ticks forward so gear-up is not delayed for hours
        if (askedTick > tickCount) askedTick = tickCount;
        if (lastHeardTick > tickCount) lastHeardTick = tickCount;
        if (tickCount - askedTick <= 1200 || tickCount - lastHeardTick <= 1200) return;
        Block stage = needsGearStage();
        if (stage == null) return;
        say("gathering wood/stone, be right back!");
        startMining(stage, 8);
    }

    public String getBuddyName() {
        return buddyName;
    }

    public void setBuddyName(String name) {
        buddyName = name;
    }

    @Override
    public void tick() {
        super.tick();
        if (level().isClientSide || !(level() instanceof ServerLevel server)) return;
        if (isMining() && tickCount % 10 == 0) mineStep(server);
        // Guard attacks every tick; on steer ticks it owns navigation and preempts follow/goto.
        boolean engaged = guardTick(server, tickCount % 20 == 0 && !isMining());
        if (engaged || isMining()) {
            gearTick();
            return;
        }
        // moveTo resets the path, so re-issue steering at most every 20 ticks.
        if (tickCount % 20 != 0) return;
        if (followUuid != null) {
            if (!(server.getPlayerByUUID(followUuid) instanceof ServerPlayer target)) {
                followUuid = null;
                return;
            }
            if (distanceToSqr(target) > 9.0D) getNavigation().moveTo(target, 1.0D);
            else getNavigation().stop();
        } else if (gotoTarget != null) {
            if (distanceToSqr(gotoTarget.getX() + 0.5D, getY(), gotoTarget.getZ() + 0.5D) < 4.0D) {
                gotoTarget = null;
                getNavigation().stop();
            } else {
                getNavigation().moveTo(
                        gotoTarget.getX() + 0.5D, gotoTarget.getY(), gotoTarget.getZ() + 0.5D, 1.0D);
            }
        }
        gearTick();
    }

    // ponytail: O(r^3) block scan every 10 ticks; shrink the radius if it ever matters.
    private void mineStep(ServerLevel server) {
        BlockPos center = blockPosition();
        BlockPos best = null;
        double bestDist = Double.MAX_VALUE;
        for (BlockPos pos : BlockPos.betweenClosed(
                center.offset(-12, -6, -12), center.offset(12, 6, 12))) {
            if (server.getBlockState(pos).is(miningBlock)) {
                double d = pos.distSqr(center);
                if (d < bestDist) {
                    bestDist = d;
                    best = pos.immutable();
                }
            }
        }
        if (best == null) return; // nothing in range — the daemon walks the body elsewhere
        if (bestDist > 16.0D) {
            getNavigation().moveTo(best.getX() + 0.5D, best.getY(), best.getZ() + 0.5D, 1.0D);
            return;
        }
        BlockState state = server.getBlockState(best);
        BlockEntity be = server.getBlockEntity(best);
        List<ItemStack> drops = Block.getDrops(state, server, best, be);
        server.destroyBlock(best, false);
        for (ItemStack drop : drops) insertStack(drop);
        if (++miningMined >= miningWant) miningBlock = null;
    }
}
