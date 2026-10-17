package dev.blockbuddy;

import java.util.List;
import java.util.UUID;

import com.google.gson.JsonObject;

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
import net.minecraft.tags.TagKey;
import net.minecraft.world.entity.monster.Monster;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.damagesource.DamageSource;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.Vec3;

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
    private TagKey<Block> miningTag;
    private String miningSelector;
    private BlockPos miningTarget;
    private int miningProgressTick;
    private int brainVoiceTicks;
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

    /** Emit one event for the brain, or one local text response. */
    public void reportEvent(String type, String item, int count, boolean ok, String fallback) {
        if (!(level() instanceof ServerLevel server)) return;
        String line;
        if (brainVoiceTicks > 0 || BuddyConfig.USE_BRAIN_VOICE.get()) {
            JsonObject event = new JsonObject();
            event.addProperty("name", buddyName);
            event.addProperty("type", type);
            event.addProperty("item", item);
            event.addProperty("count", count);
            event.addProperty("ok", ok);
            event.addProperty("fallback", fallback);
            line = "[bbevent] " + event;
        } else {
            line = "<" + buddyName + "> " + fallback;
        }
        server.getServer().getPlayerList().broadcastSystemMessage(
                net.minecraft.network.chat.Component.literal(line), false);
    }

    public void say(String text) {
        reportEvent("speech", "", 0, true, text);
    }

    public void setBrainVoice(boolean on) {
        brainVoiceTicks = on ? 1200 : 0;
    }

    @Override
    public boolean hurt(DamageSource source, float amount) {
        boolean hurt = super.hurt(source, amount);
        if (hurt && amount > 0) {
            reportEvent("hurt", source.getMsgId(), (int) Math.ceil(amount), false, "ow! that hurt.");
        }
        return hurt;
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
        if (followUuid != null) tag.putUUID("Follow", followUuid);
        if (gotoTarget != null) tag.putLong("Goto", gotoTarget.asLong());
        tag.putBoolean("GuardMode", guardMode);
        tag.putLong("LastHeardTick", lastHeardTick);
        tag.putLong("GearedTick", gearedTick);
        tag.putLong("AskedTick", askedTick);
        ContainerHelper.saveAllItems(tag, inv, level().registryAccess());
    }

    @Override
    public void readAdditionalSaveData(CompoundTag tag) {
        super.readAdditionalSaveData(tag);
        buddyName = tag.contains("BuddyName") ? tag.getString("BuddyName") : "Michael";
        ownerUuid = tag.hasUUID("Owner") ? tag.getUUID("Owner") : null;
        followUuid = tag.hasUUID("Follow") ? tag.getUUID("Follow") : null;
        gotoTarget = followUuid == null && tag.contains("Goto") ? BlockPos.of(tag.getLong("Goto")) : null;
        if (gotoTarget != null && (level().isOutsideBuildHeight(gotoTarget)
                || !level().getWorldBorder().isWithinBounds(gotoTarget))) gotoTarget = null;
        cancelMining();
        guardMode = !tag.contains("GuardMode") || tag.getBoolean("GuardMode");
        lastHeardTick = tag.getLong("LastHeardTick");
        gearedTick = tag.contains("GearedTick") ? tag.getLong("GearedTick") : -1;
        askedTick = tag.getLong("AskedTick");
        ContainerHelper.loadAllItems(tag, inv, level().registryAccess());
        markStocked();
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
        markStocked();
        return stack.getCount() - remaining;
    }

    public void setFollow(ServerPlayer player) {
        cancelMining();
        followUuid = player.getUUID();
        gotoTarget = null;
    }

    public void setGoto(BlockPos pos) {
        cancelMining();
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

    public boolean startMining(String selector, int count) {
        String value = selector.equals("log") || selector.equals("logs") || selector.equals("#logs")
                ? "#minecraft:logs" : selector;
        boolean tag = value.startsWith("#");
        ResourceLocation id = ResourceLocation.tryParse(tag ? value.substring(1) : value);
        if (id == null || (!tag && (!BuiltInRegistries.BLOCK.containsKey(id)
                || BuiltInRegistries.BLOCK.get(id) == Blocks.AIR))) return false;
        cancelMining();
        followUuid = null;
        gotoTarget = null;
        miningBlock = tag ? null : BuiltInRegistries.BLOCK.get(id);
        miningTag = tag ? TagKey.create(net.minecraft.core.registries.Registries.BLOCK, id) : null;
        miningSelector = (tag ? "#" : "") + id;
        miningWant = Math.max(1, count);
        miningMined = 0;
        miningProgressTick = tickCount;
        return true;
    }

    public boolean isMining() {
        return miningSelector != null;
    }

    public boolean isFollowing() {
        return followUuid != null;
    }

    public UUID getFollowUuid() {
        return followUuid;
    }

    private void cancelMining() {
        miningBlock = null;
        miningTag = null;
        miningSelector = null;
        miningTarget = null;
        if (!level().isClientSide) getNavigation().stop();
    }

    public void stopAll() {
        followUuid = null;
        gotoTarget = null;
        cancelMining();
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

    /** Record each command that targets this buddy. */
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

    private void markStocked() {
        if (!needsGear()) return;
        boolean wood = false;
        boolean stone = false;
        boolean iron = false;
        for (ItemStack stack : inv) {
            if (stack.isEmpty()) continue;
            String path = BuiltInRegistries.ITEM.getKey(stack.getItem()).getPath();
            wood |= path.contains("log") || path.contains("planks") || path.contains("stick");
            stone |= path.contains("cobblestone");
            iron |= path.contains("raw_iron") || path.contains("iron_ingot") || path.contains("iron_ore");
            if (wood && stone && iron) {
                markGeared();
                reportEvent("stocked", "", 0, true, "I have wood, stone, and iron.");
                return;
            }
        }
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
            reportEvent("guard", BuiltInRegistries.ENTITY_TYPE.getKey(target.getType()).toString(), 1, true,
                    "hey! leave " + owner.getGameProfile().getName() + " alone!");
        }
        return true;
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
        if (brainVoiceTicks > 0) brainVoiceTicks--;
        if (isMining() && tickCount % 10 == 0) mineStep(server);
        // Guard attacks every tick; on steer ticks it owns navigation and preempts follow/goto.
        boolean engaged = guardTick(server, tickCount % 20 == 0 && !isMining());
        if (engaged || isMining()) return;
        // moveTo resets the path, so re-issue steering at most every 20 ticks.
        if (tickCount % 20 != 0) return;
        if (followUuid != null) {
            if (!(server.getPlayerByUUID(followUuid) instanceof ServerPlayer target)) {
                getNavigation().stop();
                return;
            }
            if (distanceToSqr(target) > 9.0D) getNavigation().moveTo(target, 1.0D);
            else getNavigation().stop();
        } else if (gotoTarget != null) {
            if (distanceToSqr(Vec3.atBottomCenterOf(gotoTarget)) < 4.0D) {
                gotoTarget = null;
                getNavigation().stop();
                reportEvent("arrived", "", 0, true, "I'm here!");
            } else {
                getNavigation().moveTo(
                        gotoTarget.getX() + 0.5D, gotoTarget.getY(), gotoTarget.getZ() + 0.5D, 1.0D);
            }
        }
    }

    private boolean matchesMining(BlockState state) {
        return miningTag != null ? state.is(miningTag) : miningBlock != null && state.is(miningBlock);
    }

    private void finishMining(boolean ok, String reason) {
        String item = miningSelector;
        int mined = miningMined;
        cancelMining();
        reportEvent(ok ? "mined" : "failed", item, mined, ok,
                ok ? "mined " + mined + " " + item + "!" : reason + " Gathered " + mined + " " + item + ".");
    }

    // ponytail: scan a fixed local volume only when the current target disappears.
    private void mineStep(ServerLevel server) {
        if (miningTarget != null && !matchesMining(server.getBlockState(miningTarget))) miningTarget = null;
        if (miningTarget == null) {
            BlockPos center = blockPosition();
            double bestDist = Double.MAX_VALUE;
            for (BlockPos pos : BlockPos.betweenClosed(
                    center.offset(-12, -6, -12), center.offset(12, 6, 12))) {
                if (!matchesMining(server.getBlockState(pos))) continue;
                double distance = pos.distSqr(center);
                if (distance < bestDist) {
                    bestDist = distance;
                    miningTarget = pos.immutable();
                }
            }
        }
        if (miningTarget == null) {
            if (tickCount - miningProgressTick >= 100) finishMining(false, "I couldn't find any nearby.");
            return;
        }
        if (tickCount - miningProgressTick >= 200) {
            finishMining(false, "I couldn't reach the block.");
            return;
        }
        BlockState state = server.getBlockState(miningTarget);
        if (state.getDestroySpeed(server, miningTarget) < 0) {
            finishMining(false, "I can't break that block.");
            return;
        }
        Vec3 center = Vec3.atCenterOf(miningTarget);
        BlockHitResult hit = server.clip(new ClipContext(
                getEyePosition(), center, ClipContext.Block.COLLIDER, ClipContext.Fluid.NONE, this));
        if (distanceToSqr(center) > 16.0D || !hit.getBlockPos().equals(miningTarget)) {
            getNavigation().moveTo(center.x, miningTarget.getY(), center.z, 1.0D);
            return;
        }
        ItemStack tool = ItemStack.EMPTY;
        boolean needsTool = state.requiresCorrectToolForDrops();
        float bestSpeed = needsTool ? 0.0F : 1.0F;
        for (ItemStack stack : inv) {
            if (stack.isEmpty() || (needsTool && !stack.isCorrectToolForDrops(state))) continue;
            float speed = stack.getDestroySpeed(state);
            if (speed > bestSpeed) {
                bestSpeed = speed;
                tool = stack;
            }
        }
        if (needsTool && tool.isEmpty()) {
            finishMining(false, "I need a suitable tool to mine that block.");
            return;
        }
        BlockEntity blockEntity = server.getBlockEntity(miningTarget);
        List<ItemStack> drops = Block.getDrops(state, server, miningTarget, blockEntity, this, tool);
        if (!server.destroyBlock(miningTarget, false)) {
            finishMining(false, "I couldn't break the block.");
            return;
        }
        if (!tool.isEmpty()) tool.getItem().mineBlock(tool, server, state, miningTarget, this);
        for (ItemStack drop : drops) {
            int accepted = insertStack(drop);
            if (accepted < drop.getCount()) spawnAtLocation(drop.copyWithCount(drop.getCount() - accepted));
        }
        miningTarget = null;
        miningProgressTick = tickCount;
        if (++miningMined >= miningWant) finishMining(true, "");
    }
}
