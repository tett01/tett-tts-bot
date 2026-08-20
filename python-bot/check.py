"""실행 전 점검 — 봇을 띄우기 전에 흔한 실패 원인을 먼저 잡아냅니다.

    .venv/bin/python check.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys

FAIL = False


def ok(msg):
    print(f"  \033[32m✓\033[0m {msg}")


def bad(msg, fix=""):
    global FAIL
    FAIL = True
    print(f"  \033[31m✗\033[0m {msg}")
    if fix:
        print(f"    → {fix}")


print("\n[1/4] 실행 환경")
if shutil.which("ffmpeg"):
    ok(f"ffmpeg (시스템): {shutil.which('ffmpeg')}")
else:
    try:
        import imageio_ffmpeg

        ok(f"ffmpeg (번들): {imageio_ffmpeg.get_ffmpeg_exe()}")
    except Exception:
        bad("ffmpeg 없음 (음성이 재생되지 않습니다)",
            "requirements.txt 에 imageio-ffmpeg 를 넣고 다시 설치하세요")

try:
    import discord, edge_tts, nacl  # noqa: F401
    ok(f"discord.py {discord.__version__} / edge-tts / PyNaCl")
except ImportError as e:
    bad(f"패키지 없음: {e.name}", ".venv/bin/pip install -r requirements.txt")
    sys.exit(1)

# Opus 코덱 (음성 인코딩에 필수)
try:
    import bot as _b

    if _b.load_opus():
        ok("Opus 코덱 로드됨")
    else:
        bad("Opus 코덱 없음 (연결은 되지만 소리가 안 납니다)",
            "macOS: brew install opus / 리눅스: apt-get install libopus0")
except Exception as e:
    bad(f"Opus 확인 실패: {e}")

from dotenv import load_dotenv

load_dotenv()

print("\n[2/4] 토큰")
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
if not TOKEN or TOKEN == "여기에_봇_토큰":
    bad(".env 의 DISCORD_TOKEN 이 비어 있음",
        "개발자 포털 → Bot → Reset Token 으로 발급받아 .env 에 붙여넣으세요")
    TOKEN = ""
elif len(TOKEN) < 50:
    bad(f"토큰이 너무 짧습니다 ({len(TOKEN)}자). 잘못 복사했을 수 있습니다")
    TOKEN = ""
else:
    ok(f"토큰 읽음 ({TOKEN[:8]}…{TOKEN[-4:]})")

print("\n[3/4] edge-tts 음성 합성")


async def tts_check():
    try:
        import bot as botmod

        line = botmod.build_line("테스트 문장입니다", 0)
        path = await botmod.synthesize(line, botmod.DEFAULT_PRESET)
        size = os.path.getsize(path)
        src = discord.FFmpegPCMAudio(path, executable=botmod.FFMPEG)
        frame = src.read()
        src.cleanup()
        os.remove(path)
        if len(frame) == 3840:
            ok(f"합성 + ffmpeg 디코딩 정상 ({size}B, 프리셋 {botmod.DEFAULT_PRESET})")
        else:
            bad(f"ffmpeg 출력이 이상합니다 (프레임 {len(frame)}B, 3840이어야 함)")
    except Exception as e:
        bad(f"{type(e).__name__}: {e}")


asyncio.run(tts_check())

print("\n[4/4] 디스코드 접속")

if not TOKEN:
    print("  \033[33m—\033[0m 토큰이 없어 건너뜁니다")
    print()
    print("\033[31m토큰을 넣은 뒤 다시 실행하세요.\033[0m\n")
    sys.exit(1)

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


@client.event
async def on_ready():
    ok(f"로그인 성공: {client.user}")
    if not client.guilds:
        bad("어느 서버에도 초대되지 않았습니다",
            "개발자 포털 → OAuth2 → URL Generator 에서 초대 링크를 만드세요")
    for g in client.guilds:
        me = g.me
        need = {
            "메시지 보내기": me.guild_permissions.send_messages,
            "음성 채널 접속": me.guild_permissions.connect,
            "말하기": me.guild_permissions.speak,
        }
        missing = [k for k, v in need.items() if not v]
        if missing:
            bad(f"'{g.name}' 권한 부족: {', '.join(missing)}",
                "서버 설정 → 역할 에서 봇 역할에 권한을 주세요")
        else:
            ok(f"'{g.name}' 권한 정상")
    await client.close()


try:
    client.run(TOKEN, log_handler=None)
except discord.PrivilegedIntentsRequired:
    bad("MESSAGE CONTENT INTENT 가 꺼져 있습니다 (가장 흔한 원인)",
        "개발자 포털 → Bot → Privileged Gateway Intents → "
        "MESSAGE CONTENT INTENT 켜고 저장")
except discord.LoginFailure:
    bad("토큰이 올바르지 않습니다", "Reset Token 으로 새로 발급받으세요")
except Exception as e:
    bad(f"{type(e).__name__}: {e}")

print()
if FAIL:
    print("\033[31m문제가 있습니다. 위 → 안내를 따라 고친 뒤 다시 실행하세요.\033[0m\n")
    sys.exit(1)
print("\033[32m모두 정상입니다. .venv/bin/python bot.py 로 실행하세요.\033[0m\n")
