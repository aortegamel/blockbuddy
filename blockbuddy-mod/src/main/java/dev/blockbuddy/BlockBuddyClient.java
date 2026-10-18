package dev.blockbuddy;

import net.neoforged.api.distmarker.Dist;
import net.neoforged.bus.api.SubscribeEvent;
import net.neoforged.fml.common.EventBusSubscriber;
import net.neoforged.neoforge.client.event.ClientChatReceivedEvent;

/** Client-only: hide brain-protocol lines from the chat screen. Never referenced from common code. */
@EventBusSubscriber(modid = "blockbuddy", value = Dist.CLIENT)
public final class BlockBuddyClient {
    private BlockBuddyClient() {}

    @SubscribeEvent
    public static void onChat(ClientChatReceivedEvent event) {
        String text = event.getMessage().getString();
        if (text.startsWith("[bbevent]") || text.startsWith("[agentstate]")) {
            event.setCanceled(true);
        }
    }
}
