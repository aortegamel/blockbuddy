package dev.blockbuddy;

import net.neoforged.fml.config.ModConfig;
import net.neoforged.neoforge.common.ModConfigSpec;

/** Common settings for companion names and the optional brain voice. */
public final class BuddyConfig {

    public static final ModConfigSpec SPEC;
    public static final ModConfigSpec.ConfigValue<String> companionName;
    public static final ModConfigSpec.BooleanValue USE_BRAIN_VOICE;

    static {
        ModConfigSpec.Builder b = new ModConfigSpec.Builder();
        b.translation("blockbuddy.configuration.title");
        companionName = b.translation("blockbuddy.configuration.companionName").define("companionName", "Michael");
        USE_BRAIN_VOICE = b.translation("blockbuddy.configuration.useBrainVoice").define("useBrainVoice", false);
        SPEC = b.build();
    }

    private BuddyConfig() {}

    public static ModConfig.Type type() {
        return ModConfig.Type.COMMON;
    }

}
