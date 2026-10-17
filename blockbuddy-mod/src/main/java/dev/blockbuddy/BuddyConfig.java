package dev.blockbuddy;

import java.nio.file.Path;
import net.neoforged.fml.config.ModConfig;
import net.neoforged.fml.loading.FMLPaths;
import net.neoforged.neoforge.common.ModConfigSpec;

/** CLIENT config. companionName renames Michael; voiceEnabled gates the Kokoro opt-in flow. */
public final class BuddyConfig {

    public static final ModConfigSpec SPEC;
    public static final ModConfigSpec.ConfigValue<String> companionName;
    public static final ModConfigSpec.BooleanValue voiceEnabled;
    public static final ModConfigSpec.ConfigValue<String> voiceEngine;
    public static final ModConfigSpec.ConfigValue<String> voiceName;
    public static final ModConfigSpec.IntValue proximityRange;
    public static final ModConfigSpec.BooleanValue serverFeatures;

    public static final String MODEL_FILE = "kokoro-v1_0.onnx";
    // ponytail: 340MB total (322MB onnx + 18MB voices); confirm text matches before download
    public static final String MODEL_URL =
            "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx";
    public static final String VOICES_FILE = "voices-v1.0.bin";
    public static final String VOICES_URL =
            "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin";
    public static final String APPROX_MB = "340MB";

    static {
        ModConfigSpec.Builder b = new ModConfigSpec.Builder();
        b.translation("blockbuddy.configuration.title");
        companionName = b.translation("blockbuddy.configuration.companionName").define("companionName", "Michael");
        voiceEnabled = b.translation("blockbuddy.configuration.voiceEnabled").define("voiceEnabled", false);
        voiceEngine = b.translation("blockbuddy.configuration.voiceEngine").define("voiceEngine", "kokoro");
        voiceName = b.translation("blockbuddy.configuration.voiceName").define("voiceName", "am_michael");
        proximityRange = b.translation("blockbuddy.configuration.proximityRange").defineInRange("proximityRange", 32, 4, 64);
        serverFeatures = b.comment("Gate for future daemon/agent features; Michael's own chat always allowed")
                .translation("blockbuddy.configuration.serverFeatures").define("serverFeatures", false);
        SPEC = b.build();
    }

    private BuddyConfig() {}

    public static ModConfig.Type type() {
        return ModConfig.Type.CLIENT;
    }

    /** <gamedir>/mods/blockbuddy-voice — instance-local Kokoro cache. */
    public static Path modelDir() {
        return FMLPaths.GAMEDIR.get().resolve("mods").resolve("blockbuddy-voice");
    }

    public static Path modelFile() {
        return modelDir().resolve(MODEL_FILE);
    }

    public static Path voicesFile() {
        return modelDir().resolve(VOICES_FILE);
    }
}
