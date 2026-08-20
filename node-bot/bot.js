/**
 * Discord TTS 봇 — msedge-tts (Microsoft Edge 신경망 음성) 기반
 * 한국어 여성: ko-KR-SunHiNeural / 영어 여성: en-US-AriaNeural
 * 실행: npm install && npm start
 */
import "dotenv/config";
import { Client, GatewayIntentBits, Events } from "discord.js";
import {
  joinVoiceChannel,
  createAudioPlayer,
  createAudioResource,
  entersState,
  StreamType,
  AudioPlayerStatus,
  VoiceConnectionStatus,
  getVoiceConnection,
} from "@discordjs/voice";
import { MsEdgeTTS, OUTPUT_FORMAT } from "msedge-tts";

const KO_VOICE = "ko-KR-SunHiNeural";
const EN_VOICE = "en-US-AriaNeural";
const RATE = "+8%";
const PITCH = "+0Hz";
const MAX_CHARS = 200;

const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildVoiceStates,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.MessageContent, // 개발자 포털에서 켜야 함
  ],
});

/** 길드별 상태: { player, queue, playing } */
const guilds = new Map();

const hasHangul = (t) => /[가-힣]/.test(t);

function clean(text) {
  return text
    .replace(/https?:\/\/\S+/g, "링크")
    .replace(/<a?:\w+:\d+>/g, "")
    .replace(/<[@#!&]+\d+>/g, "")
    .trim()
    .slice(0, MAX_CHARS);
}

/** edge-tts로 mp3 스트림 생성 */
async function synthesize(text) {
  const tts = new MsEdgeTTS();
  await tts.setMetadata(
    hasHangul(text) ? KO_VOICE : EN_VOICE,
    OUTPUT_FORMAT.AUDIO_24KHZ_48KBITRATE_MONO_MP3
  );
  const result = tts.toStream(text, { rate: RATE, pitch: PITCH });
  // msedge-tts 버전에 따라 스트림을 직접 주거나 { audioStream }으로 감싸서 줌
  return result.audioStream ?? result;
}

function getState(guildId) {
  if (!guilds.has(guildId)) {
    const player = createAudioPlayer();
    const state = { player, queue: [], playing: false };
    player.on(AudioPlayerStatus.Idle, () => {
      state.playing = false;
      drain(guildId);
    });
    player.on("error", (e) => {
      console.error("[재생 오류]", e.message);
      state.playing = false;
      drain(guildId);
    });
    guilds.set(guildId, state);
  }
  return guilds.get(guildId);
}

/** 큐를 하나씩 꺼내 순서대로 재생 */
async function drain(guildId) {
  const state = getState(guildId);
  if (state.playing || state.queue.length === 0) return;

  state.playing = true;
  const text = state.queue.shift();
  try {
    const stream = await synthesize(text);
    const resource = createAudioResource(stream, {
      inputType: StreamType.Arbitrary, // mp3 → ffmpeg가 변환
    });
    state.player.play(resource);
  } catch (e) {
    console.error("[TTS 오류]", e.message);
    state.playing = false;
    drain(guildId);
  }
}

client.once(Events.ClientReady, (c) => console.log(`로그인 완료: ${c.user.tag}`));

client.on(Events.MessageCreate, async (message) => {
  if (message.author.bot || !message.guild) return;

  // ── 명령어 ──
  if (message.content === "!join" || message.content === "!들어와") {
    const channel = message.member?.voice?.channel;
    if (!channel) return message.reply("먼저 음성 채널에 들어가 주세요.");

    const connection = joinVoiceChannel({
      channelId: channel.id,
      guildId: message.guild.id,
      adapterCreator: message.guild.voiceAdapterCreator,
      selfDeaf: true,
    });
    await entersState(connection, VoiceConnectionStatus.Ready, 20_000);
    connection.subscribe(getState(message.guild.id).player);
    return message.reply(`\`${channel.name}\` 입장. 이제 이 채널의 글을 읽어드립니다.`);
  }

  if (message.content === "!leave" || message.content === "!나가") {
    const connection = getVoiceConnection(message.guild.id);
    if (!connection) return message.reply("음성 채널에 있지 않습니다.");
    connection.destroy();
    guilds.delete(message.guild.id);
    return message.reply("나갔습니다.");
  }

  if (message.content.startsWith("!")) return;

  // ── 일반 메시지 읽기 ──
  const connection = getVoiceConnection(message.guild.id);
  if (!connection) return;

  const userChannel = message.member?.voice?.channelId;
  if (userChannel !== connection.joinConfig.channelId) return;

  const text = clean(message.content);
  if (!text) return;

  getState(message.guild.id).queue.push(text);
  drain(message.guild.id);
});

client.login(process.env.DISCORD_TOKEN);
