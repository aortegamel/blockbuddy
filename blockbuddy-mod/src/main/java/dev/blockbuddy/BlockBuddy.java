package dev.blockbuddy;

import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.entity.MobCategory;
import net.minecraftforge.api.distmarker.Dist;
import net.minecraftforge.client.event.EntityRenderersEvent;
import net.minecraftforge.common.MinecraftForge;
import net.minecraftforge.event.RegisterCommandsEvent;
import net.minecraftforge.event.entity.EntityAttributeCreationEvent;
import net.minecraftforge.eventbus.api.SubscribeEvent;
import net.minecraftforge.fml.common.Mod;
import net.minecraftforge.fml.javafmlmod.FMLJavaModLoadingContext;
import net.minecraftforge.registries.DeferredRegister;
import net.minecraftforge.registries.ForgeRegistries;
import net.minecraftforge.registries.RegistryObject;

@Mod("blockbuddy")
public class BlockBuddy {

    private static final DeferredRegister<EntityType<?>> ENTITIES =
            DeferredRegister.create(ForgeRegistries.ENTITY_TYPES, "blockbuddy");

    public static final RegistryObject<EntityType<BlockBuddyEntity>> BUDDY =
            ENTITIES.register("buddy", () -> EntityType.Builder.<BlockBuddyEntity>of(
                    BlockBuddyEntity::new, MobCategory.MISC)
                    .sized(0.6F, 1.8F)
                    .build("buddy"));

    public BlockBuddy() {
        ENTITIES.register(FMLJavaModLoadingContext.get().getModEventBus());
        MinecraftForge.EVENT_BUS.register(this);
    }

    @SubscribeEvent
    public void onRegisterCommands(RegisterCommandsEvent event) {
        BlockBuddyCommand.register(event.getDispatcher());
    }

    @Mod.EventBusSubscriber(modid = "blockbuddy", bus = Mod.EventBusSubscriber.Bus.MOD)
    public static final class Attrs {
        @SubscribeEvent
        public static void onAttributes(EntityAttributeCreationEvent event) {
            event.put(BUDDY.get(), BlockBuddyEntity.attributes());
        }
    }

    // Never loaded on a dedicated server (Dist.CLIENT subscriber).
    @Mod.EventBusSubscriber(modid = "blockbuddy", value = Dist.CLIENT,
            bus = Mod.EventBusSubscriber.Bus.MOD)
    public static final class ClientRenders {
        @SubscribeEvent
        public static void onRegisterRenderers(EntityRenderersEvent.RegisterRenderers event) {
            event.registerEntityRenderer(BUDDY.get(), ctx ->
                    new net.minecraft.client.renderer.entity.HumanoidMobRenderer<
                            BlockBuddyEntity,
                            net.minecraft.client.model.HumanoidModel<BlockBuddyEntity>>(
                            ctx,
                            new net.minecraft.client.model.HumanoidModel<>(
                                    ctx.bakeLayer(
                                            net.minecraft.client.model.geom.ModelLayers.PLAYER)),
                            0.5F) {
                        @Override
                        public ResourceLocation getTextureLocation(BlockBuddyEntity entity) {
                            return new ResourceLocation("minecraft",
                                    "textures/entity/zombie/zombie.png");
                        }
                    });
        }
    }
}
