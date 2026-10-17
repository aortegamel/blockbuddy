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

/**
 * Built-in companion body: a server-side mob with its own inventory,
 * vanilla pathfinding and vanilla block breaking. No external jars.
 */
public class BlockBuddyEntity extends PathfinderMob {

    /** Player-inventory-sized storage (36 main slots). */
    public final NonNullList<ItemStack> inv = NonNullList.withSize(36, ItemStack.EMPTY);

    private String buddyName = "Agent";
    private UUID followUuid;
    private BlockPos gotoTarget;
    private Block miningBlock;
    private int miningWant;
    private int miningMined;

    public BlockBuddyEntity(EntityType<? extends PathfinderMob> type, Level level) {
        super(type, level);
    }

    public static AttributeSupplier attributes() {
        return Mob.createMobAttributes()
                .add(Attributes.MAX_HEALTH, 20.0D)
                .add(Attributes.MOVEMENT_SPEED, 0.32D)
                .add(Attributes.FOLLOW_RANGE, 48.0D)
                .build();
    }

    @Override
    public void addAdditionalSaveData(CompoundTag tag) {
        super.addAdditionalSaveData(tag);
        tag.putString("BuddyName", buddyName);
        ContainerHelper.saveAllItems(tag, inv);
    }

    @Override
    public void readAdditionalSaveData(CompoundTag tag) {
        super.readAdditionalSaveData(tag);
        buddyName = tag.getString("BuddyName");
        ContainerHelper.loadAllItems(tag, inv);
    }

    /** Merge a stack into inv; returns the amount accepted. */
    public int insertStack(ItemStack stack) {
        int remaining = stack.getCount();
        for (ItemStack slot : inv) {
            if (remaining == 0) break;
            if (!slot.isEmpty() && ItemStack.isSameItemSameTags(slot, stack)
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
        // moveTo resets the path, so re-issue steering at most every 20 ticks.
        if (tickCount % 20 != 0 || isMining()) return;
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
