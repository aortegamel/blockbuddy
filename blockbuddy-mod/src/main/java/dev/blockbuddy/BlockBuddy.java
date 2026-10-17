package dev.blockbuddy;

import net.minecraft.core.registries.Registries;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.entity.MobCategory;
import net.neoforged.api.distmarker.Dist;
import net.neoforged.bus.api.IEventBus;
import net.neoforged.bus.api.SubscribeEvent;
import net.neoforged.fml.ModContainer;
import net.neoforged.fml.common.EventBusSubscriber;
import net.neoforged.fml.common.Mod;
import net.neoforged.neoforge.client.event.EntityRenderersEvent;
import net.neoforged.neoforge.client.gui.ConfigurationScreen;
import net.neoforged.neoforge.client.gui.IConfigScreenFactory;
import net.neoforged.neoforge.common.NeoForge;
import net.neoforged.neoforge.event.RegisterCommandsEvent;
import net.neoforged.neoforge.event.entity.EntityAttributeCreationEvent;
import net.neoforged.neoforge.registries.DeferredHolder;
import net.neoforged.neoforge.registries.DeferredRegister;
@Mod("blockbuddy")
public class BlockBuddy {

    private static final DeferredRegister<EntityType<?>> ENTITIES =
            DeferredRegister.create(Registries.ENTITY_TYPE, "blockbuddy");

    public static final DeferredHolder<EntityType<?>, EntityType<BlockBuddyEntity>> BUDDY =
            ENTITIES.register("buddy", () -> EntityType.Builder.<BlockBuddyEntity>of(
                    BlockBuddyEntity::new, MobCategory.MISC)
                    .sized(0.6F, 1.8F)
                    .build("buddy"));

    public BlockBuddy(IEventBus modBus, ModContainer container) {
        ENTITIES.register(modBus);
        container.registerConfig(BuddyConfig.type(), BuddyConfig.SPEC);
        NeoForge.EVENT_BUS.register(this);
        // Supplier variant, client only: classes resolve when the Mods menu opens, never on a server.
        if (net.neoforged.fml.loading.FMLEnvironment.dist == Dist.CLIENT) {
            java.util.function.Supplier<IConfigScreenFactory> screen =
                    () -> (c, parent) -> new ConfigurationScreen(c, parent);
            container.registerExtensionPoint(IConfigScreenFactory.class, screen);
        }
    }

    @SubscribeEvent
    public void onRegisterCommands(RegisterCommandsEvent event) {
        BlockBuddyCommand.register(event.getDispatcher());
    }

    @EventBusSubscriber(modid = "blockbuddy", bus = EventBusSubscriber.Bus.MOD)
    public static final class Attrs {
        @SubscribeEvent
        public static void onAttributes(EntityAttributeCreationEvent event) {
            event.put(BUDDY.get(), BlockBuddyEntity.attributes());
        }
    }

    // Never loaded on a dedicated server (Dist.CLIENT subscriber).
    @EventBusSubscriber(modid = "blockbuddy", value = Dist.CLIENT,
            bus = EventBusSubscriber.Bus.MOD)
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
                            return ResourceLocation.fromNamespaceAndPath("minecraft",
                                    "textures/entity/zombie/zombie.png");
                        }
                    });
        }
    }
}
