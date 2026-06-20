# VPBuddy 源代码

VPBuddy 实现目录。

## 当前状态

**MVP Step 1 完成 ✅**(2026-06-20)
- `vpbuddy/state.py`:MeetingState(单一可信源,5 类累积项)
- `vpbuddy/storage.py`:JSON 持久化(NFS)
- `tests/test_state.py`:16 个测试,全通过

详见 [`vpbuddy/README.md`](vpbuddy/README.md)

## 目录

```
src/
├── vpbuddy/
│   ├── __init__.py
│   ├── state.py         # MeetingState
│   ├── storage.py       # MeetingStorage
│   └── README.md
└── tests/
    └── test_state.py
```

## 跑测试

```bash
PYTHONPATH=src python3 -m pytest src/tests/test_state.py -v
```

## 下一步

Step 2: 自接音频流 + Whisper + pyannote(见 `docs/research/asr-speaker-diarization-survey.md` v2)
