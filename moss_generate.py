#!/usr/bin/env python3
"""사진 지시문 → DGX1 moss(ComfyUI) 이미지 생성 (고르곤 사내 스킬).

사용법:
  python3 moss_generate.py "프롬프트" [--out 이름] [--w 1344] [--h 768]
                          [--steps 25] [--cfg 3.0] [--seed N]
  python3 moss_generate.py --recover PROMPT_ID [--out 이름]

- 모델: moss_image_sft_bf16 (깨끗한 기본 모델, 검증 완료)
- 프롬프트는 영문 권장 (한글도 가능하나 영문이 안정적)
- 결과: output/<이름>.png 저장 후 전체 경로 출력
- 생성 시간: 약 8분 (25스텝 실측 475초)
- 실행 제한(30초) 환경에서는 2단계 흐름 사용:
  ① 제출: 위 명령 실행 → prompt_id 출력(접수는 서버에 남고 계속 생성됨)
  ② 회수: 1~2분 후 `--recover PROMPT_ID --out 이름` 를 반복 실행(수 초 소요)
     "아직 생성 중"이 나오면 다시 실행. 완료분은 언제든 회수 가능.
"""
import argparse, json, os, random, re, sys, time, urllib.request

COMFY_URL = os.environ.get("MOSS_URL", "http://100.108.60.5:8188")
CKPT = "moss_image_sft_bf16.safetensors"
CLIP = "qwen3.5_9b_int8_convrot.safetensors"
VAE = "flux2-vae.safetensors"
NEGATIVE = ("nude, nudity, naked, nsfw, revealing clothing, "
            "AI generated, cartoon, anime, plastic, doll, deformed, extra limbs, "
            "bad anatomy, blurry, watermark, text, multiple people, 3 arms, extra arm, "
            "extra fingers, low quality")


def post(ep, data):
    req = urllib.request.Request(f"{COMFY_URL}{ep}", data=json.dumps(data).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req).read())
    except urllib.error.HTTPError as e:
        print("HTTP 오류 %d: %s" % (e.code, e.read().decode()[:600])); raise


def get_json(ep):
    return json.loads(urllib.request.urlopen(f"{COMFY_URL}{ep}").read())


def save_image(fname, subfolder, ftype, out):
    url = f"{COMFY_URL}/view?filename={urllib.request.quote(fname)}&subfolder={urllib.request.quote(subfolder)}&type={ftype}"
    data = urllib.request.urlopen(url).read()
    out_dir = os.path.join(os.getcwd(), "output")
    os.makedirs(out_dir, exist_ok=True)
    base = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", out or fname)[:80]
    out_path = os.path.join(out_dir, base + ".png")
    with open(out_path, "wb") as f:
        f.write(data)
    return out_path, len(data)


def find_outputs(pid):
    """(완료)→(파일명, 하위폴더, 타입) / (접수됨·미완료)→() / (기록 없음)→None."""
    h = get_json(f"/history/{urllib.request.quote(pid)}")
    if pid not in h:
        return None
    for node in h[pid].get("outputs", {}).values():
        for img in node.get("images", []):
            return img["filename"], img.get("subfolder", ""), img.get("type", "output")
    return ()


def recover(pid, out):
    hit = find_outputs(pid.strip())
    if hit is None:
        print("접수 기록 없음: %s" % pid); sys.exit(2)
    if not hit:
        print("아직 생성 중 — 1~2분 후 같은 명령을 다시 실행하세요."); sys.exit(3)
    path, size = save_image(*hit, out)
    print("회수 완료: %s (%d바이트)" % (path, size))
    print(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt", nargs="?", default=None)
    ap.add_argument("--recover", default=None, help="기존 접수(prompt_id)의 완료 이미지 회수")
    ap.add_argument("--out", default=None)
    ap.add_argument("--w", type=int, default=1344)
    ap.add_argument("--h", type=int, default=768)
    ap.add_argument("--steps", type=int, default=25)
    ap.add_argument("--cfg", type=float, default=3.0)
    ap.add_argument("--shift", type=float, default=5.0)
    ap.add_argument("--seed", type=int, default=random.randint(1, 2 ** 31 - 1))
    a = ap.parse_args()

    if a.recover:
        recover(a.recover, a.out)
        return
    if not a.prompt:
        raise SystemExit('사용법: moss_generate.py "프롬프트" [--out 이름] | '
                         'moss_generate.py --recover PROMPT_ID [--out 이름]')

    wf = {
        "1": {"inputs": {"unet_name": CKPT, "weight_dtype": "default"}, "class_type": "UNETLoader"},
        "2": {"inputs": {"clip_name": CLIP, "type": "moss_image", "device": "default"}, "class_type": "CLIPLoader"},
        "3": {"inputs": {"vae_name": VAE}, "class_type": "VAELoader"},
        "10": {"inputs": {"shift": a.shift, "model": ["1", 0]}, "class_type": "ModelSamplingAuraFlow"},
        "4": {"inputs": {"text": a.prompt, "clip": ["2", 0]}, "class_type": "CLIPTextEncode"},
        "5": {"inputs": {"text": NEGATIVE, "clip": ["2", 0]}, "class_type": "CLIPTextEncode"},
        "6": {"inputs": {"width": a.w, "height": a.h, "batch_size": 1}, "class_type": "EmptyLatentImage"},
        "7": {"inputs": {"seed": a.seed, "steps": a.steps, "cfg": a.cfg, "sampler_name": "euler",
                         "scheduler": "sgm_uniform", "denoise": 1.0, "model": ["10", 0],
                         "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["6", 0]},
              "class_type": "KSampler"},
        "8": {"inputs": {"samples": ["7", 0], "vae": ["3", 0]}, "class_type": "VAEDecode"},
        "9": {"inputs": {"filename_prefix": "moss_news", "images": ["8", 0]}, "class_type": "SaveImage"},
    }
    pid = post("/prompt", {"prompt": wf})["prompt_id"]
    print("접수 prompt_id=%s seed=%d %dx%d %d스텝" % (pid, a.seed, a.w, a.h, a.steps), flush=True)
    print("30초 제한 환경이면 여기서 중단해도 되고, 몇 분 후 --recover %s --out %s 로 회수하세요."
          % (pid, a.out or "moss_이름"), flush=True)

    t0 = time.time()
    hit = None
    while time.time() - t0 < 1800:
        time.sleep(10)
        try:
            hit = find_outputs(pid)
        except Exception as e:
            print("조회 재시도: %s (%ds)" % (e, int(time.time() - t0)), flush=True); continue
        if hit:
            break
        print("진행 중 %ds..." % int(time.time() - t0), flush=True)
    if not hit:
        raise SystemExit("생성 시간 초과 (30분) — 접수는 서버에 남아 있습니다. "
                         "나중에 --recover %s 로 회수하세요." % pid)

    path, size = save_image(*hit, a.out)
    print("생성 완료: %s (%d바이트, %d초)" % (path, size, int(time.time() - t0)))
    print(path)


if __name__ == "__main__":
    main()
