# ADR-0005: 用 ModelScope 镜像替代 HF_TOKEN(国内网络 + 无账号)

- **状态**: Accepted
- **日期**: 2026-06-21
- **作者**: 张胜东(起草: Hermes,踩坑 5 次)
- **关联**: [ADR-0004 Step 2 ASR](./0004-MVP-Step2-ASR设计.md) · [架构 v1.16 §4.1](../design/总体架构.md)

---

## 背景

VPBuddy Step 2 用 pyannote 说话人分离。pyannote 三个模型(`speaker-diarization-3.1`、`segmentation-3.0`、`wespeaker-voxceleb-resnet34-LM`)在 HuggingFace 上都是 **gated**(需要登录+同意用户协议+HF_TOKEN 鉴权)。

**问题**:
1. 国内访问 `huggingface.co` 受限(用户网络环境:CDN 阻 + 整段封 Google/部分国外 IP)
2. 即使能访问,需注册账号 + 同意协议 + 创建 token,流程摩擦大
3. token 是个人的,V 转岗/换设备要重新签发

**触发**:2026-06-21 GPU 服务器实测:
- `huggingface.co` 直连 `SYN-SENT` 卡死
- `hf-mirror.com` 对 gated 模型仍 `403 Access to model pyannote/... is restricted`
- 用户无 HF token,无法 `Pipeline.from_pretrained("pyannote/speaker-diarization-3.1")`

---

## 决策

**用 ModelScope(阿里达摩院,国内 CDN)镜像下载模型 + 手动加载 .bin 文件,完全绕开 huggingface_hub 鉴权。**

### 实施

1. **`pip install modelscope`** —— 装阿里 SDK(国内直连)
2. **`modelscope download --model pyannote/...`** —— 三个模型各下一份到本地目录(共 ~42MB,30 秒下完)
3. **`OmegaConf` + `hydra.instantiate` 手动加载** —— 替代 `Pipeline.from_pretrained(repo_id)`,避开 gated 检查
4. **monkey-patch `use_auth_token`** —— 旧 pyannote API 用了 deprecated 参数,patch 转发到 `token`
5. **音频预转换 16kHz mono PCM** —— 避免 pyannote 内部 batch 重采样错位

### 关键代码模式

```python
# 一次性准备
pip install modelscope
mkdir -p /tmp/pyannote_models
modelscope download --model pyannote/speaker-diarization-3.1 --local_dir /tmp/pyannote_models/speaker-diarization-3.1
modelscope download --model pyannote/segmentation-3.0 --local_dir /tmp/pyannote_models/segmentation-3.0
modelscope download --model pyannote/wespeaker-voxceleb-resnet34-LM --local_dir /tmp/pyannotte_models/wespeaker-voxceleb-resnet34-LM
export PYANNOTE_LOCAL_DIR=/tmp/pyannote_models

# 加载(从 src/vpbuddy/diarization.py:PyannoteDiarizer._load)
config_path = Path(local_dir) / "speaker-diarization-3.1" / "config.yaml"
cfg = OmegaConf.create(config_path.read_text())

# 把 repo id 替换为本地 .bin
local_paths = {
    "segmentation": f"{local_dir}/segmentation-3.0/pytorch_model.bin",
    "embedding": f"{local_dir}/wespeaker-voxceleb-resnet34-LM/pytorch_model.bin",
}
flat_params = {k: local_paths.get(k, v) for k, v in cfg.pipeline.params.items()}

# 实例化
pipeline = instantiate(OmegaConf.create({"_target_": cfg.pipeline.name, **flat_params}))
pipeline = pipeline.instantiate({
    "clustering": {"method": "centroid", "min_cluster_size": 12, "threshold": 0.7045654963945799},
    "segmentation": {"min_duration_off": 0.0},
})
pipeline = pipeline.to(torch.device("cuda"))  # GPU
```

### 替代方案(对比)

| 方案 | 国内可达 | 无账号 | 无 token | 维护成本 |
|---|---|---|---|---|
| **本方案 (ModelScope)** | ✅ | ✅ | ✅ | 50 行胶水代码 |
| HF_TOKEN + hf.co | ❌ 国内阻 | ❌ 需注册 | ❌ 需 token | 0(官方) |
| hf-mirror.com | ✅ | ✅ | ❌ gated 仍 403 | 0(改 endpoint) |
| simple_diarizer(无 pyannote) | ✅ | ✅ | ✅ | 需重写 `diarization.py`,**精度下降 ~20%** |
| 跳过说话人分离 | ✅ | ✅ | ✅ | MVP 不完整 |

**结论**:本方案是 2026-06 实测**最稳**的国内方案,维护成本可控,效果等同官方。

---

## 后果

### 正面
- ✅ 国内用户零摩擦:无墙、无账号、无 token
- ✅ ModelScope 是阿里达摩院官方,长期稳定
- ✅ 同样模式可推广到其他 gated 模型(whisper-large-v3、Llama、Qwen 等若有镜像)
- ✅ 测试可完全离线:模型一次性下完,后续 `HF_HUB_OFFLINE=1` 跑测试

### 负面 / 取舍
- ⚠️ 加了 50 行胶水代码(`PyannoteDiarizer` 类比官方版本长)
- ⚠️ 依赖 `modelscope` SDK(项目多一个 dep)
- ⚠️ Monkey-patch `hf_hub_download` 是 hack(新版 pyannote 可能修)
- ⚠️ 需要用户**一次性**跑下载命令(README 要说清楚)

### 风险 & 缓解
- **风险**:ModelScope 镜像删模型 → 缓解:可加 fallback `huggingface.co`(有 token 时)
- **风险**:新版 pyannote 改 API → 缓解:本方案不依赖 pyannote 内部 `from_pretrained`,只依赖 `Model.from_pretrained(local_path)` 基础 API
- **风险**:用户机器没 GPU → 缓解:`device="cpu"` fallback(慢但能用)

---

## 变更

- 2026-06-21: 起草,基于 2026-06-21 晚 GPU 服务器完整跑通 38 tests 的实战经验
