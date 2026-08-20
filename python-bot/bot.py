"""
Discord TTS 봇 — edge-tts (Microsoft Edge 신경망 음성) 기반
- 완전 무료 / 무제한 / API 키 불필요
- 한글이 섞이면 한국어 음성, 아니면 영어 음성으로 자동 전환

필요: ffmpeg
실행: python bot.py
"""

from __future__ import annotations

import asyncio
import ctypes.util
import fcntl
import json
import os
import sys
import re
import shutil
import tempfile
from collections import defaultdict

import discord
import edge_tts

import chime
from discord.ext import commands
from dotenv import load_dotenv

# 파일·호스팅 콘솔로 출력이 넘어가면 파이썬이 버퍼링을 해서 로그가 늦게 보입니다.
# 디스호스트 콘솔에서 상태를 바로 확인하려면 줄 단위로 흘려보내야 합니다.
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except AttributeError:      # 아주 오래된 파이썬 대비
    pass

# 토큰은 두 경로 모두 지원합니다.
#  1) 호스팅 패널의 환경변수 (있으면 이쪽이 안전)
#  2) 같은 폴더의 .env 파일 (패널에 환경변수 기능이 없을 때)
# 실행 위치가 어디든 스크립트 폴더의 .env를 찾도록 경로를 명시합니다.
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))


def resolve_ffmpeg() -> str:
    """ffmpeg 실행 파일을 찾습니다.

    디스호스트처럼 셸 접속이 없는 호스팅에는 ffmpeg가 없을 수 있습니다.
    시스템에 없으면 pip으로 함께 설치되는 imageio-ffmpeg 번들을 씁니다.
    """
    system = shutil.which("ffmpeg")
    if system:
        return system
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        raise SystemExit(
            "ffmpeg를 찾을 수 없습니다. requirements.txt에 imageio-ffmpeg가 "
            "포함되어 있는지 확인하세요."
        )


_lock_handle = None


def acquire_single_instance_lock() -> bool:
    """같은 폴더에서 봇이 두 번 실행되는 것을 막습니다.

    같은 토큰으로 두 곳에서 접속하면 디스코드가 막지 않고 양쪽 다
    이벤트를 받습니다. 그러면 같은 말을 두 번 읽고 음성 채널을 서로
    뺏습니다. 파일 잠금은 같은 머신 안에서만 유효하므로, 로컬과
    호스팅을 동시에 켜는 것까지는 막지 못합니다.
    """
    global _lock_handle
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.lock")
    try:
        _lock_handle = open(path, "w")
        fcntl.flock(_lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_handle.write(str(os.getpid()))
        _lock_handle.flush()
        return True
    except OSError:
        return False       # 다른 프로세스가 이미 잠갔습니다
    except Exception:
        return True        # 잠금을 못 걸어도 봇 자체는 돌아가야 합니다


FFMPEG = resolve_ffmpeg()


def load_opus() -> bool:
    """Opus 코덱을 불러옵니다.

    디스코드 음성은 Opus로 인코딩해야 합니다. discord.py는 윈도우용
    라이브러리만 동봉하고, macOS·리눅스에서는 시스템 것을 찾아야 하는데
    ctypes의 기본 탐색 경로에 Homebrew(/opt/homebrew/lib)가 없어서
    설치돼 있어도 못 찾는 경우가 많습니다. 후보 경로를 직접 훑습니다.
    """
    if discord.opus.is_loaded():
        return True

    found = ctypes.util.find_library("opus")
    candidates = [found] if found else []
    candidates += [
        # macOS (Homebrew: Apple Silicon / Intel)
        "/opt/homebrew/lib/libopus.dylib",
        "/usr/local/lib/libopus.dylib",
        # 리눅스 (디스호스트 등 호스팅 환경)
        "libopus.so.0",
        "/usr/lib/x86_64-linux-gnu/libopus.so.0",
        "/usr/lib/aarch64-linux-gnu/libopus.so.0",
        "/usr/lib/libopus.so.0",
        "/usr/local/lib/libopus.so.0",
    ]
    for path in candidates:
        try:
            discord.opus.load_opus(path)
            if discord.opus.is_loaded():
                print(f"Opus 로드: {path}")
                return True
        except Exception:
            continue

    print(
        "[경고] Opus 코덱을 찾지 못했습니다. 음성이 재생되지 않습니다.\n"
        "  macOS: brew install opus\n"
        "  리눅스: apt-get install libopus0  (호스팅이면 운영진에 문의)"
    )
    return False

# ── 음성 프리셋 ──────────────────────────────────────────────
# edge-tts에 실제 존재하는 음성만 담았습니다. (2026-08 확인)
# ko-KR 여성은 SunHi 하나뿐이라, 다국어 여성 음성을 대안으로 넣었습니다.
PRESETS = {
    # ── 대화형(Conversation) 음성 ─────────────────────────────
    # MS가 'Conversation + Copilot' 용도로 튜닝한 음성. 낭독조가 아니라
    # 사람과 대화하는 톤입니다. 한국어도 발화하지만 원어민 음성은 아닙니다.
    "yeonha": {
        "ko": "en-US-AvaMultilingualNeural",
        "en": "en-US-AvaMultilingualNeural",
        "rate": "+6%", "pitch": "+22Hz",
        "desc": "밝고 어린 대화 톤 · 추임새와 가장 잘 맞음 (기본)",
    },
    "ava": {
        "ko": "en-US-AvaMultilingualNeural",
        "en": "en-US-AvaMultilingualNeural",
        "rate": "+3%", "pitch": "+2Hz",
        "desc": "대화형 여성 · 표현력 풍부, 차분한 편",
    },
    "emma": {
        "ko": "en-US-EmmaMultilingualNeural",
        "en": "en-US-EmmaMultilingualNeural",
        "rate": "+3%", "pitch": "+0Hz",
        "desc": "대화형 여성 · 차분하고 따뜻한 구어체 톤",
    },
    # ── 한국어 원어민 음성 ────────────────────────────────────
    # 발음·억양은 정확하지만 카테고리가 'General'(낭독용)이라
    # 대화 톤은 위 둘보다 딱딱합니다.
    "sunhi": {
        "ko": "ko-KR-SunHiNeural",
        "en": "en-US-AriaNeural",
        "rate": "+10%", "pitch": "+8Hz",
        "desc": "한국어 원어민 여성 · 발음 정확, 톤은 다소 또박또박",
    },
    "news": {
        "ko": "ko-KR-SunHiNeural",
        "en": "en-US-AriaNeural",
        "rate": "+0%", "pitch": "+0Hz",
        "desc": "한국어 원어민 여성 · 보정 없는 원본 낭독조",
    },
}
DEFAULT_PRESET = "yeonha"
MAX_CHARS = 200  # 한 번에 읽을 최대 길이

intents = discord.Intents.default()
intents.message_content = True  # 개발자 포털에서 MESSAGE CONTENT INTENT 활성화 필요
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

queues: dict[int, asyncio.Queue] = defaultdict(asyncio.Queue)
workers: dict[int, asyncio.Task] = {}
guild_preset: dict[int, str] = defaultdict(lambda: DEFAULT_PRESET)
guild_chime: dict[int, float] = defaultdict(lambda: 1.0)  # 추임새 강도 0=끔
guild_auto: dict[int, bool] = defaultdict(lambda: True)   # 자동 입장 여부

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")


def load_settings():
    """서버별 설정을 불러옵니다. 여러 서버에서 쓸 때 재시작마다 초기화되면
    매번 다시 맞춰야 하므로 파일로 남깁니다."""
    if not os.path.exists(SETTINGS_FILE):
        return
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        for gid, cfg in data.items():
            if cfg.get("preset") in PRESETS:
                guild_preset[int(gid)] = cfg["preset"]
            if isinstance(cfg.get("chime"), (int, float)):
                guild_chime[int(gid)] = float(cfg["chime"])
            if isinstance(cfg.get("auto"), bool):
                guild_auto[int(gid)] = cfg["auto"]
        print(f"설정 불러옴: {len(data)}개 서버")
    except Exception as e:
        print(f"[설정 불러오기 실패] {e}")


def save_settings():
    """설정을 저장합니다. 실패해도 봇 동작에는 지장이 없어야 합니다."""
    try:
        gids = set(guild_preset) | set(guild_chime) | set(guild_auto)
        data = {
            str(g): {
                "preset": guild_preset[g],
                "chime": guild_chime[g],
                "auto": guild_auto[g],
            }
            for g in gids
        }
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SETTINGS_FILE)   # 쓰는 도중 중단돼도 원본이 깨지지 않도록
    except Exception as e:
        print(f"[설정 저장 실패] {e}")


def strip_noise(text: str) -> str:
    """URL·멘션·커스텀 이모지 등 읽어봐야 소용없는 것을 걷어냅니다.

    추임새 판별이 'ㅋㅋ'·'ㅠㅠ' 같은 기호에 의존하므로 이 단계에서는
    기호를 그대로 남겨 둡니다. 구어체 변환은 humanize()가 맡습니다.
    """
    text = re.sub(r"https?://\S+", "링크", text)
    text = re.sub(r"<a?:(\w+):\d+>", r"\1", text)  # 커스텀 이모지 → 이름만
    text = re.sub(r"<[@#!&]+\d+>", "", text)       # 멘션/채널
    return re.sub(r"\s+", " ", text).strip()


def humanize(text: str) -> str:
    """낭독체로 읽히기 쉬운 채팅 표기를 구어체 신호로 바꿉니다.

    TTS는 문장부호를 억양·호흡 신호로 씁니다. 채팅 표기를 그대로 넣으면
    'ㅋㅋㅋ'를 "크크크"로 읽거나, '...'에서 부자연스럽게 오래 멈춥니다.
    """
    text = re.sub(r"[ㅋㅎ]{2,}", ", 하하,", text)      # 웃음 표기 → 실제 웃음
    text = re.sub(r"[ㅜㅠ]{2,}", "", text)             # 우는 표기는 읽지 않음
    text = re.sub(r"([!?])\1{1,}", r"\1", text)       # !!! → !
    text = re.sub(r"\.{2,}", ",", text)               # ... → 짧은 쉼
    text = re.sub(r"~+", "", text)                    # 물결 제거
    # 초성체 풀기. \b 는 한글에서 신뢰할 수 없어 반복형까지 직접 처리합니다.
    # (ㄱㅅㄱㅅ, ㅇㅇㅇ 처럼 늘려 쓰는 경우가 흔합니다)
    for short, full in [("ㄱㅅ", "고마워"), ("ㅈㅅ", "미안"), ("ㅇㅇ", "응"),
                        ("ㄴㄴ", "아니"), ("ㅇㅋ", "오케이"), ("ㄱㄱ", "가자"),
                        ("ㅅㄱ", "수고"), ("ㅊㅋ", "축하해")]:
        text = re.sub(f"(?:{short})+", full, text)
    text = re.sub(r"ㅇ{3,}", "응", text)
    # 문두 접속사·감탄사 뒤에 쉼표를 넣어 사람의 호흡을 만듭니다
    text = re.sub(r"^(야|근데|그니까|그래서|아니|어|음|아)\s", r"\1, ", text)
    text = text.replace(". ", ", ")                   # 문장 끊김 완화
    return tidy(text)


def tidy(text: str) -> str:
    """치환 과정에서 생긴 쉼표 중복·문두 쉼표 등을 정리합니다.

    'ㅋㅋㅋ 웃겨' 처럼 기호가 문두에 있으면 ', 하하, 웃겨' 가 되어
    문장이 쉼표로 시작하거나 쉼표가 겹칩니다. TTS는 이걸 어색한
    정적으로 읽으므로 반드시 정리해야 합니다.
    """
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.!?])", r"\1", text)        # 쉼표 앞 공백
    text = re.sub(r"(,\s*){2,}", ", ", text)          # 쉼표 중복
    text = re.sub(r"^[\s,]+", "", text)               # 문두 쉼표
    text = re.sub(r"[\s,]+$", "", text)               # 문말 쉼표
    return text.strip()


def build_line(raw: str, guild_id: int) -> str:
    """읽을 문장을 만듭니다: 노이즈 제거 → 추임새 → 구어체 변환.

    순서가 중요합니다. 추임새는 원문의 'ㅋㅋ'·'ㅠㅠ'로 분위기를 읽으므로
    humanize()가 그 기호를 바꾸기 '전에' 붙여야 합니다.
    """
    text = strip_noise(raw)
    if not text:
        return ""
    text = chime.add_chime(text, guild_id, guild_chime[guild_id])
    return humanize(text)[:MAX_CHARS]


# 여러 서버가 동시에 떠들면 edge-tts가 일시 차단될 수 있어 동시 요청을 묶습니다.
tts_lock = asyncio.Semaphore(3)


async def synthesize(text: str, preset_name: str) -> str:
    """edge-tts로 mp3를 만들고 임시 파일 경로를 돌려줍니다.

    비공식 엔드포인트라 간헐적으로 실패합니다. 한 번의 실패로 메시지를
    통째로 놓치지 않도록 짧게 재시도합니다.
    """
    p = PRESETS[preset_name]
    voice = p["ko"] if re.search(r"[가-힣]", text) else p["en"]

    last_error = None
    for attempt in range(3):
        try:
            async with tts_lock:
                communicate = edge_tts.Communicate(
                    text, voice=voice, rate=p["rate"], pitch=p["pitch"]
                )
                fd, path = tempfile.mkstemp(suffix=".mp3")
                os.close(fd)
                await communicate.save(path)

            if os.path.getsize(path) > 0:
                return path
            os.remove(path)
            raise RuntimeError("빈 오디오")
        except Exception as e:
            last_error = e
            await asyncio.sleep(0.6 * (attempt + 1))

    raise RuntimeError(f"TTS 3회 실패: {last_error}")


async def player(guild_id: int):
    """길드마다 하나씩 돌면서 큐를 순서대로 재생합니다.

    음성 연결(voice_client)은 재접속·채널 이동으로 교체될 수 있으므로
    붙잡아 두지 않고 매번 새로 조회합니다.
    """
    queue = queues[guild_id]
    while True:
        text = await queue.get()
        path = None
        try:
            guild = bot.get_guild(guild_id)
            vc = guild.voice_client if guild else None
            if not vc or not vc.is_connected():
                continue   # 연결이 끊긴 동안 들어온 메시지는 버립니다

            path = await synthesize(text, guild_preset[guild_id])

            done = asyncio.Event()

            def after(err):
                if err:
                    print(f"[재생 오류] {err}")
                bot.loop.call_soon_threadsafe(done.set)

            vc.play(discord.FFmpegPCMAudio(path, executable=FFMPEG), after=after)
            await done.wait()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[TTS 오류] guild={guild_id} {type(e).__name__}: {e}")
        finally:
            if path and os.path.exists(path):
                os.remove(path)
            queue.task_done()


async def ensure_connected(guild: discord.Guild, channel: discord.VoiceChannel):
    """해당 음성 채널에 봇을 연결하고 재생 태스크를 띄웁니다.

    이미 다른 채널에 있고 거기에 사람이 있으면 옮기지 않습니다.
    (다른 사람들이 쓰는 중인데 뺏어오면 안 되니까요)
    """
    vc = guild.voice_client

    if vc and vc.is_connected():
        if vc.channel == channel:
            pass
        else:
            others = [u for u in vc.channel.voice_states if u != bot.user.id]
            if others:
                return None          # 다른 채널에서 사용 중 — 그대로 둡니다
            await vc.move_to(channel)
    else:
        vc = await channel.connect()

    gid = guild.id
    if gid not in workers or workers[gid].done():
        workers[gid] = asyncio.create_task(player(gid))
    return vc


@bot.event
async def on_ready():
    load_opus()
    load_settings()
    print(f"로그인 완료: {bot.user} (서버 {len(bot.guilds)}개)")
    for g in bot.guilds:
        print(f"  · {g.name} — 목소리 {guild_preset[g.id]}, 추임새 {guild_chime[g.id]}")

    # 슬래시 명령어 등록. 전역 동기화는 반영에 최대 1시간이 걸리므로,
    # 들어가 있는 서버에는 개별로 밀어넣어 즉시 뜨게 합니다.
    for g in bot.guilds:
        try:
            bot.tree.copy_global_to(guild=g)
            synced = await bot.tree.sync(guild=g)
            print(f"  · {g.name} — 슬래시 명령어 {len(synced)}개 등록")
        except discord.Forbidden:
            print(f"  · {g.name} — 슬래시 명령어 등록 실패 "
                  f"(초대 링크에 applications.commands 범위가 빠졌습니다)")
        except Exception as e:
            print(f"  · {g.name} — 슬래시 명령어 등록 실패: {e}")


@bot.hybrid_command(name="join", aliases=["들어와", "ㅈ"],
                    description="내가 있는 음성 채널로 봇을 부릅니다")
async def join(ctx: commands.Context):
    """내가 있는 음성 채널로 봇을 부릅니다."""
    # 음성 연결은 수 초가 걸릴 수 있습니다. 슬래시 명령어는 3초 안에
    # 응답하지 않으면 만료(10062)되므로 먼저 지연 응답을 보냅니다.
    await ctx.defer()

    if not ctx.author.voice:
        return await ctx.send("먼저 음성 채널에 들어가 주세요.")

    channel = ctx.author.voice.channel
    vc = await ensure_connected(ctx.guild, channel)
    if vc is None:
        return await ctx.send("다른 음성채널에서 실행중입니다.")

    gid = ctx.guild.id
    await ctx.send(
        f"`{channel.name}` 입장했습니다. 이제 이 채널에 쓰는 글을 읽어드릴게요.\n"
        f"현재 목소리: **{guild_preset[gid]}** — {PRESETS[guild_preset[gid]]['desc']}\n"
        f"추임새: 켜짐 (`!추임새` 로 조절)"
    )


@bot.hybrid_command(name="leave", aliases=["나가", "ㄴ"],
                    description="봇을 음성 채널에서 내보냅니다")
async def leave(ctx: commands.Context):
    """음성 채널에서 나갑니다."""
    await ctx.defer()

    if not ctx.voice_client:
        return await ctx.send("음성 채널에 있지 않습니다.")

    gid = ctx.guild.id
    task = workers.pop(gid, None)
    if task:
        task.cancel()
    queues.pop(gid, None)
    await ctx.voice_client.disconnect()
    await ctx.send("나갔습니다.")


# ── 설정 패널 ────────────────────────────────────────────────
CHIME_LEVELS = {
    "끔": 0.0, "적게": 0.5, "보통": 1.0, "많이": 1.6, "항상": 2.5,
}


def chime_label(value: float) -> str:
    """추임새 강도 수치를 사람이 읽는 이름으로 바꿉니다."""
    for name, v in CHIME_LEVELS.items():
        if abs(v - value) < 0.01:
            return name
    return f"{value:g}"


def settings_embed(guild: discord.Guild) -> discord.Embed:
    """현재 설정을 한눈에 보여줍니다."""
    gid = guild.id
    preset = PRESETS[guild_preset[gid]]
    e = discord.Embed(
        title="TTS 설정",
        description=f"**{guild.name}** 서버의 현재 설정입니다.",
        color=0x5865F2,
    )
    e.add_field(
        name="목소리",
        value=f"**{guild_preset[gid]}**\n{preset['desc']}",
        inline=False,
    )
    e.add_field(
        name="추임새",
        value=f"**{chime_label(guild_chime[gid])}**",
        inline=True,
    )
    e.add_field(
        name="자동 입장",
        value="**켜짐**" if guild_auto[gid] else "**꺼짐**",
        inline=True,
    )
    e.set_footer(text="아래에서 바로 바꿀 수 있습니다 · 2분 후 조작이 만료됩니다")
    return e


class VoiceSelect(discord.ui.Select):
    def __init__(self, gid: int):
        options = [
            discord.SelectOption(
                label=name,
                description=cfg["desc"][:100],
                default=(name == guild_preset[gid]),
            )
            for name, cfg in PRESETS.items()
        ]
        super().__init__(placeholder="목소리 고르기", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        guild_preset[interaction.guild.id] = self.values[0]
        save_settings()
        await interaction.response.edit_message(
            embed=settings_embed(interaction.guild),
            view=SettingsView(interaction.guild.id),
        )


class ChimeSelect(discord.ui.Select):
    def __init__(self, gid: int):
        current = chime_label(guild_chime[gid])
        options = [
            discord.SelectOption(label=name, default=(name == current))
            for name in CHIME_LEVELS
        ]
        super().__init__(placeholder="추임새 빈도", options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        guild_chime[interaction.guild.id] = CHIME_LEVELS[self.values[0]]
        save_settings()
        await interaction.response.edit_message(
            embed=settings_embed(interaction.guild),
            view=SettingsView(interaction.guild.id),
        )


class AutoButton(discord.ui.Button):
    def __init__(self, gid: int):
        on = guild_auto[gid]
        super().__init__(
            label="자동 입장 끄기" if on else "자동 입장 켜기",
            style=discord.ButtonStyle.danger if on else discord.ButtonStyle.success,
            row=2,
        )

    async def callback(self, interaction: discord.Interaction):
        gid = interaction.guild.id
        guild_auto[gid] = not guild_auto[gid]
        save_settings()
        await interaction.response.edit_message(
            embed=settings_embed(interaction.guild), view=SettingsView(gid)
        )


class SettingsView(discord.ui.View):
    def __init__(self, gid: int):
        super().__init__(timeout=120)
        self.add_item(VoiceSelect(gid))
        self.add_item(ChimeSelect(gid))
        self.add_item(AutoButton(gid))


@bot.hybrid_command(name="settings", aliases=["설정"],
                    description="목소리·추임새·자동입장을 한 곳에서 설정합니다")
async def settings_cmd(ctx: commands.Context):
    """설정 패널을 엽니다."""
    await ctx.send(
        embed=settings_embed(ctx.guild), view=SettingsView(ctx.guild.id)
    )


@bot.hybrid_command(name="voice", aliases=["목소리"],
                    description="목소리를 바꿉니다 (yeonha/ava/emma/sunhi/news)")
async def voice(ctx: commands.Context, name: str = None):
    """목소리를 바꿉니다. 예) !voice ava"""
    gid = ctx.guild.id
    if name is None or name not in PRESETS:
        lines = [
            f"{'▶' if k == guild_preset[gid] else '　'} `{k}` — {v['desc']}"
            for k, v in PRESETS.items()
        ]
        return await ctx.send(
            "**사용 가능한 목소리** (`!voice 이름` 으로 변경)\n" + "\n".join(lines)
        )

    guild_preset[gid] = name
    save_settings()
    await ctx.send(f"목소리를 **{name}** 으로 바꿨습니다 — {PRESETS[name]['desc']}")


@bot.hybrid_command(name="chime", aliases=["추임새"],
                    description="추임새 빈도를 조절합니다 (끔/적게/보통/많이/항상)")
async def chime_cmd(ctx: commands.Context, level: str = None):
    """추임새 빈도를 조절합니다. 예) !추임새 많이"""
    gid = ctx.guild.id
    levels = dict(CHIME_LEVELS, off=0.0, on=1.0)

    if level is None or level not in levels:
        cur = guild_chime[gid]
        name = next((k for k, v in levels.items() if v == cur), f"{cur}")
        return await ctx.send(
            f"현재 추임새: **{name}**\n"
            "`!추임새 끔 / 적게 / 보통 / 많이 / 항상` 으로 조절합니다."
        )

    guild_chime[gid] = levels[level]
    save_settings()
    if levels[level] == 0:
        return await ctx.send("추임새를 껐습니다. 이제 원문만 읽습니다.")
    await ctx.send(f"추임새를 **{level}** 로 설정했습니다.")


@bot.hybrid_command(name="auto", aliases=["자동"],
                    description="음성 채널 채팅에 글을 쓰면 자동으로 들어올지 설정합니다")
async def auto_cmd(ctx: commands.Context, mode: str = None):
    """자동 입장을 켜고 끕니다. 예) !자동 끔"""
    gid = ctx.guild.id
    if mode is None or mode.lower() not in ("켬", "on", "끔", "off"):
        state = "켜짐" if guild_auto[gid] else "꺼짐"
        return await ctx.send(
            f"자동 입장: **{state}**\n"
            "켜져 있으면 음성 채널 채팅창에 글을 쓸 때 봇이 알아서 들어와 읽습니다.\n"
            "`!자동 켬` / `!자동 끔` 으로 바꿉니다."
        )

    guild_auto[gid] = mode.lower() in ("켬", "on")
    save_settings()
    if guild_auto[gid]:
        await ctx.send("자동 입장을 켰습니다. 음성 채널 채팅에 글을 쓰면 들어갑니다.")
    else:
        await ctx.send("자동 입장을 껐습니다. `!join` 으로 직접 불러 주세요.")


@bot.hybrid_command(name="say", aliases=["말해"],
                    description="입력한 내용을 읽어줍니다")
async def say(ctx: commands.Context, *, text: str):
    """명령어로 직접 읽히기. 예) !say 안녕하세요"""
    if not ctx.voice_client:
        return await ctx.send("먼저 `!join` 으로 봇을 불러 주세요.")
    line = build_line(text, ctx.guild.id)
    if line:
        await queues[ctx.guild.id].put(line)


@bot.hybrid_command(name="skip", aliases=["넘겨"],
                    description="지금 읽고 있는 문장을 건너뜁니다")
async def skip(ctx: commands.Context):
    """지금 읽고 있는 문장을 건너뜁니다."""
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.message.add_reaction("⏭️")


@bot.hybrid_command(name="help", aliases=["도움말"],
                    description="명령어 목록을 보여줍니다")
async def help_cmd(ctx: commands.Context):
    await ctx.send(
        "**TTS 봇 명령어**\n"
        "`!join` — 음성 채널로 부르기\n"
        "`!leave` — 내보내기\n"
        "`!설정` — 설정 패널 (목소리·추임새·자동입장 한 번에)\n"
        "`!voice` — 목소리 목록 / 변경\n"
        "`!추임새 많이` — 추임새 빈도 조절 (끔/적게/보통/많이/항상)\n"
        "`!자동 켬/끔` — 음성 채널 채팅 자동 입장\n"
        "`!say 내용` — 직접 읽히기\n"
        "`!skip` — 현재 문장 건너뛰기\n"
        "\n**자동 입장**: 음성 채널의 내장 채팅창에 글을 쓰면 봇이 알아서 들어와 읽습니다.\n"
        "일반 텍스트 채널에서는 `!join` 으로 부른 뒤 사용하세요."
    )


@bot.event
async def on_command_error(ctx: commands.Context, error):
    """명령어 오류를 사용자에게 알립니다. 조용히 실패하면 원인 찾기가 어렵습니다."""
    if isinstance(error, commands.CommandNotFound):
        return
    original = getattr(error, "original", error)
    print(f"[명령어 오류] {ctx.command}: {type(original).__name__}: {original}")
    try:
        await ctx.send(f"명령어 처리 중 오류가 났습니다: `{type(original).__name__}`")
    except Exception:
        pass


@bot.event
async def on_guild_join(guild: discord.Guild):
    """새 서버에 초대되면 슬래시 명령어를 바로 등록합니다."""
    try:
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        print(f"새 서버 참여: {guild.name} — 슬래시 명령어 등록 완료")
    except Exception as e:
        print(f"새 서버 참여: {guild.name} — 슬래시 명령어 등록 실패: {e}")


@bot.event
async def on_message(message: discord.Message):
    await bot.process_commands(message)

    if message.author.bot or not message.guild:
        return
    if message.content.startswith("!"):
        return

    gid = message.guild.id
    vc = message.guild.voice_client
    author_channel = message.author.voice.channel if message.author.voice else None

    # ── 자동 입장 ──────────────────────────────────────────────
    # 음성 채널에는 내장 채팅창이 있습니다. 글쓴이가 그 음성 채널에
    # 접속해 있고, 그 채널의 채팅창에 쓴 것이라면 봇을 불러들입니다.
    if (
        guild_auto[gid]
        and author_channel is not None
        and isinstance(message.channel, discord.VoiceChannel)
        and message.channel == author_channel
        and (vc is None or not vc.is_connected() or vc.channel != author_channel)
    ):
        try:
            vc = await ensure_connected(message.guild, author_channel)
            if vc is None:
                return          # 다른 채널에서 사용 중
            print(f"자동 입장: {message.guild.name} / {author_channel.name}")
        except Exception as e:
            print(f"[자동 입장 실패] {type(e).__name__}: {e}")
            return

    # ── 읽기 ──────────────────────────────────────────────────
    if not vc or not vc.is_connected():
        return
    # 봇과 같은 음성 채널에 있는 사람의 말만 읽습니다
    if author_channel != vc.channel:
        return

    text = build_line(message.content, gid)
    if text:
        await queues[gid].put(text)


@bot.event
async def on_voice_state_update(member, before, after):
    """음성 채널에 사람이 아무도 없으면 자동으로 나갑니다.

    주의: channel.members 는 멤버 캐시에 의존하는데, 이 봇은 특권 인텐트인
    members 를 켜지 않으므로 캐시에 없는 사용자가 빠집니다. 그걸로 인원을
    세면 '봇 혼자 남았다'고 오판해서 들어오자마자 나가버립니다.
    캐시와 무관한 voice_states(음성 상태 원본)로 세야 합니다.
    """
    if member.id == bot.user.id:
        return                      # 봇 자신의 입·퇴장은 무시

    guild = member.guild
    vc = guild.voice_client
    if not vc or not vc.is_connected():
        return

    # 우리가 있는 채널에서 '나간' 경우만 확인합니다
    if before.channel != vc.channel or after.channel == vc.channel:
        return

    await asyncio.sleep(3)          # 잠깐 나갔다 오는 경우를 위해 여유를 둡니다

    vc = guild.voice_client
    if not vc or not vc.is_connected():
        return

    humans = [uid for uid in vc.channel.voice_states if uid != bot.user.id]
    if humans:
        return                      # 아직 사람이 있으면 그대로 둡니다

    task = workers.pop(guild.id, None)
    if task:
        task.cancel()
    queues.pop(guild.id, None)
    await vc.disconnect()
    print(f"자동 퇴장: {guild.name} (음성 채널에 아무도 없음)")


if __name__ == "__main__":
    if not acquire_single_instance_lock():
        raise SystemExit(
            "봇이 이미 다른 곳에서 실행중입니다.\n"
            "  같은 토큰으로 두 번 켜면 같은 말을 두 번 읽고 음성 채널을 서로 뺏습니다.\n"
            "  먼저 실행 중인 봇을 끄고 다시 시도하세요:  pkill -f bot.py"
        )

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN이 없습니다. .env 파일을 만들어 토큰을 넣어 주세요.")
    bot.run(token)
