"""Reusable creator-page uploader. Final publish is exclusively a human action."""
import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT / "vendor")]
from common import atomic_json, file_hash, load_config
from publication import clean_path, load_bundle


class ManualStep(RuntimeError):
    pass


def candidates(page, selectors, *, visible=True, container_visible=False):
    for selector in selectors:
        for frame in page.frames:
            try:
                locator = frame.locator(selector)
                available = [locator.nth(i) for i in range(locator.count())
                             if (not visible or locator.nth(i).is_visible()) and locator.nth(i).is_enabled()
                             and (not container_visible or locator.nth(i).evaluate("e=>{for(let p=e.parentElement;p;p=p.parentElement){let s=getComputedStyle(p);if(s.display==='none'||s.visibility==='hidden')return false}return true}"))]
                if len(available) == 1:
                    return available[0]
                if len(available) > 1:
                    # An ambiguous page is not a reason to overwrite the first
                    # field or to upload into a random file input.
                    raise ManualStep(f"页面上有多个匹配项：{selector}，请手动完成或更新选择器。")
            except ManualStep:
                raise
            except Exception:
                continue
    return None


def wait_for(page, finder, seconds, check=lambda: None):
    end = time.monotonic() + seconds
    last_error = None
    while time.monotonic() < end:
        check()
        try:
            value = finder()
            last_error = None
        except ManualStep as exc:
            last_error = exc
            value = None
        if value is not None and value is not False:
            return value
        page.wait_for_timeout(500)
    if last_error is not None:
        raise last_error
    return None


def safe_click(locator, *, cover_confirmation=False):
    text = re.sub(r"\s+", "", locator.inner_text())
    if re.search(r"发布|投稿|提交审核|提交作品", text):
        raise ManualStep("该按钮涉及发布，留给人工确认。")
    allowed = {"上传视频", "添加封面", "设置封面", "编辑封面", "修改封面", "更改封面", "上传封面",
               "上传图片", "自选封面", "本地上传"}
    if cover_confirmation:
        dialog_text = locator.evaluate("""e => {
          const editor=e.closest('.cover-editor'), footer=e.closest('.cover-editor-button');
          const head=editor?.querySelector('.cover-editor-head');
          const labels=footer?Array.from(footer.children).map(c=>c.innerText.trim()):[];
          if(editor&&footer&&head?.innerText.trim()==='封面制作'
             &&editor.querySelectorAll('.editor_4_3,.editor_16_9').length===2
             &&labels.length===2&&labels.includes('取消')&&labels.includes('完成'))
            return head.innerText+' '+footer.innerText;
          return e.closest('[role=dialog],.el-dialog,.bili-modal,.bcc-dialog,.cover-modal,.cover-editor')?.innerText || '';
        }""")
        if "封面" not in dialog_text or re.search(r"发布|投稿|提交审核|提交作品", dialog_text):
            raise ManualStep("无法确定这是封面裁剪窗口，请人工确认。")
        allowed |= {"确定", "确认", "完成", "保存封面", "确认封面", "应用"}
        if ("4:3封面" in dialog_text and "16:9封面" in dialog_text
                and "同步" in dialog_text):
            allowed.add("确认同步")
    if text not in allowed:
        raise ManualStep(f"按钮未在允许操作范围内：{text[:60]}")
    locator.click(timeout=7000)


def click_named(page, labels, *, cover_confirmation=False, scope=None):
    scopes = [scope] if scope is not None else page.frames
    for label in labels:
        for frame in scopes:
            for locator in (frame.get_by_role("button", name=label, exact=True), frame.get_by_text(label, exact=True)):
                items = [locator.nth(i) for i in range(locator.count())
                         if locator.nth(i).is_visible() and locator.nth(i).is_enabled()]
                if len(items) == 1:
                    if items[0].evaluate("e => !!(e.closest('label') || e).querySelector('input[type=file]')"):
                        continue
                    if not items[0].evaluate("e => !!e.closest('button,[role=button],a,.button,.cover-edit-entry,.cover-slot') || !!e.onclick || getComputedStyle(e).cursor==='pointer'"):
                        continue
                    safe_click(items[0], cover_confirmation=cover_confirmation)
                    return True
                if len(items) > 1:
                    continue
    return False


def fill_exact(locator, value):
    limit = locator.get_attribute("maxlength")
    if limit and limit.isdigit() and len(value) > int(limit):
        raise ManualStep(f"页面字段限制 {limit} 字，请先缩短文本。")
    locator.fill(value, timeout=10000)
    tag = locator.evaluate("e => e.tagName.toLowerCase()")
    actual = locator.input_value() if tag in ("input", "textarea") else locator.inner_text()
    expected = value.replace("\r\n", "\n").rstrip("\n")
    matches = actual.replace("\r\n", "\n").rstrip("\n") == expected
    if not matches and tag not in ("input", "textarea"):
        # A plain contenteditable may visually collapse LF in a text node.
        # Text content still preserves the exact draft; do not mistake that
        # rendering behavior for truncation.
        matches = locator.text_content().replace("\r\n", "\n").rstrip("\n") == expected
        if not matches:
            # Chromium adds block/paragraph separators to innerText. Check all
            # nonblank lines, retaining every character within each credit.
            lines = lambda text: [line.rstrip().replace("\u00a0", " ") for line in text.splitlines() if line.strip()]
            matches = lines(actual) == lines(expected)
    if not matches:
        raise ManualStep("网页实际写入的文本与草稿不同，请手动核对，助手不会截断内容。")


def image_signature(page, selectors):
    result = []
    for frame in page.frames:
        for selector in selectors:
            try:
                result += frame.locator(selector).evaluate_all(
                    "es => es.flatMap(e=>{if(e.tagName==='IMG')return e.complete&&e.naturalWidth>0?[e.currentSrc||e.src]:[];"
                    "let b=getComputedStyle(e).backgroundImage;return b&&b!=='none'?[b]:[]})")
            except Exception:
                pass
    return sorted(set(result))


def open_cover(page, settings):
    for selector in settings.get("cover_hover", []):
        locator = candidates(page, [selector])
        if locator is not None:
            locator.hover(timeout=5000)
    if click_named(page, settings.get("cover_open", [])):
        return True
    locator = candidates(page, settings.get("cover_open_selector", []))
    if locator is not None and locator.evaluate("e => !!e.closest('[class*=cover]')"):
        if re.search(r"发布|投稿|提交审核|提交作品", locator.inner_text()):
            raise ManualStep("封面入口包含发布操作，请人工处理。")
        locator.click(timeout=7000)
        return True
    return False


def confirm_cover_dialog(page):
    # Confirm only inside an identified cover dialog. Never use a page-wide
    # '确定'/'完成' click; the page may also contain a publish confirmation.
    for frame in page.frames:
        # This footer belongs to the identified dual-ratio cover editor. Its
        # sample feed can contain video titles mentioning 发布/投稿; those titles
        # do not determine the meaning of this editor's 完成 action.
        footer = frame.locator(".cover-editor .cover-editor-button")
        if footer.count() == 1 and footer.is_visible():
            button = footer.get_by_text("完成", exact=True)
            if button.count() == 1 and button.is_enabled():
                safe_click(button, cover_confirmation=True)
                return True
        sync = frame.locator(".videoup-confirm-modal")
        for modal in sync.all():
            text = modal.inner_text() if modal.is_visible() else ""
            if ("4:3封面" in text and "16:9封面" in text and "同步" in text
                    and not re.search(r"发布|投稿|提交审核|提交作品", text)):
                return click_named(page, ("确认同步",), cover_confirmation=True, scope=modal)
        dialogs = frame.locator("[role='dialog'],.el-dialog,.bili-modal,.bcc-dialog,.cover-modal,.cover-editor")
        for i in range(dialogs.count()):
            dialog = dialogs.nth(i)
            text = dialog.inner_text() if dialog.is_visible() else ""
            if "封面" in text and not re.search(r"发布|投稿|提交审核|提交作品", text):
                return click_named(page, ("确定", "确认", "完成", "保存封面", "确认封面", "应用"),
                                   cover_confirmation=True, scope=dialog)
    return False


def upload_finished(page, settings):
    for frame in page.frames:
        for selector in settings.get("upload_ready", []):
            if frame.locator(selector).count() == 1 and frame.locator(selector).is_visible():
                return True
    for frame in page.frames:
        for selector in settings.get("upload_done", []):
            for item in frame.locator(selector).all():
                if item.is_visible():
                    text = item.inner_text()
                    if re.search(r"上传(?:完成|成功)|(?:上传\s*)?100\s*%", text) and not re.search(r"失败|异常", text):
                        return True
    return False


def cover_dialog_open(page):
    for frame in page.frames:
        if frame.locator(".cover-editor,.videoup-confirm-modal,[role=dialog],.el-dialog,.cover-modal").evaluate_all(
                "es=>es.some(e=>e.getBoundingClientRect().height>0&&getComputedStyle(e).visibility!=='hidden'&&e.innerText.includes('封面'))"):
            return True
    return False


def reset_bilibili_cover_zoom(page, ratios=("4-3", "16-9")):
    """Reset only the two named cover zoom sliders, never the video timeline."""
    values = {}
    for ratio in ratios:
        slider = candidates(page, [f".cover-editor .scale-slider-{ratio} [role=slider]"])
        if slider is None:
            continue
        if slider.get_attribute("aria-valuemin") != "0" or slider.get_attribute("aria-valuemax") != "1":
            raise ManualStep("封面缩放范围已变化，请人工检查。")
        slider.press("Home", timeout=5000)
        if not wait_for(page, lambda: float(slider.get_attribute("aria-valuenow") or "nan") == 0, 3):
            raise ManualStep("未能重置封面缩放，请人工检查。")
        values[ratio] = 0
    return values


def cover_canvas_error(pane, image):
    """Compare the rendered cover with the selected asset before saving."""
    encoded = "data:image/jpeg;base64," + base64.b64encode(Path(image).read_bytes()).decode("ascii")
    return pane.locator("canvas.lower-canvas").evaluate("""async (canvas, source) => {
      const expected = new Image(); expected.src = source; await expected.decode();
      // B站 keeps a 16:9 canvas underneath its 4:3 crop rectangle. Compare
      // the exported rectangle, not the black margins outside that rectangle.
      const box=canvas.closest('.cover-editor-panel-canvas-image,.editor_4_3,.editor_16_9')?.querySelector('.crop-box-fixed');
      const bounds=canvas.getBoundingClientRect(), crop=box?.getBoundingClientRect();
      const area=crop?{x:(crop.left-bounds.left)*canvas.width/bounds.width,y:(crop.top-bounds.top)*canvas.height/bounds.height,
        w:crop.width*canvas.width/bounds.width,h:crop.height*canvas.height/bounds.height}:{x:0,y:0,w:canvas.width,h:canvas.height};
      if(Math.abs(area.w/area.h-expected.naturalWidth/expected.naturalHeight)>0.02)return 1000;
      const actual=document.createElement('canvas');actual.width=Math.round(area.w);actual.height=Math.round(area.h);
      actual.getContext('2d').drawImage(canvas,area.x,area.y,area.w,area.h,0,0,actual.width,actual.height);
      const reference=document.createElement('canvas');reference.width=actual.width;reference.height=actual.height;
      const ref=reference.getContext('2d');ref.fillStyle='black';ref.fillRect(0,0,reference.width,reference.height);
      ref.drawImage(expected,0,0,reference.width,reference.height);
      const sample = image => {const c=document.createElement('canvas');c.width=c.height=64;
        const ctx=c.getContext('2d');ctx.fillStyle='black';ctx.fillRect(0,0,64,64);
        ctx.drawImage(image,0,0,64,64);return ctx.getImageData(0,0,64,64).data;};
      const a=sample(actual), b=sample(reference);let sum=0,count=0;
      for(let i=0;i<a.length;i++){if(i%4!==3){sum+=(a[i]-b[i])**2;count++;}}
      return Math.sqrt(sum/count);
    }""", encoded)


def upload_bilibili_covers(page, settings, bundle, variants, check=lambda: None):
    """Upload each ratio separately, disable synchronization, and verify pixels."""
    if not cover_dialog_open(page):
        if not open_cover(page, settings) or not wait_for(page, lambda: cover_dialog_open(page), 10, check):
            raise ManualStep("无法打开 B站封面编辑器。")
    editor = candidates(page, [".cover-editor"])
    if editor is None or "封面制作" not in editor.inner_text():
        raise ManualStep("未找到唯一的 B站双比例封面编辑器。")
    verification = {}
    for ratio, suffix in (("4:3", "4_3"), ("16:9", "16_9")):
        check()
        pane = editor.locator(f".cover-editor-panel-canvas > div:has(.scale-slider-{suffix.replace('_', '-')})")
        if pane.count() != 1:
            raise ManualStep(f"未找到唯一的 {ratio} 封面画布。")
        heading = pane.locator(".cover-editor-panel-canvas-title .text")
        if heading.count() != 1 or ratio not in heading.inner_text():
            raise ManualStep("封面比例入口已变化。")
        heading.click(timeout=5000)
        empty_input = pane.locator(".cover-editor-panel-canvas-empty input[type=file][accept*=image]")
        first_image = empty_input.count() == 1
        if not first_image and not wait_for(page, lambda: pane.evaluate("e=>e.classList.contains('active')"), 5, check):
            raise ManualStep(f"未能切换到 {ratio} 封面。")
        checkbox = pane.locator(".sync-checkbox input[type=checkbox]")
        if checkbox.count() != 1:
            raise ManualStep("未找到双比例同步选项。")
        sync = pane.locator(".sync-checkbox")
        sync_on = lambda: sync.evaluate("e=>e.classList.contains('bcc-checkbox-checked') || e.querySelector('input').checked")
        if sync_on():
            sync.click(timeout=5000)
        if not wait_for(page, lambda: not sync_on(), 3, check):
            raise ManualStep("双比例同步未关闭，不能分别上传封面。")
        image = Path(bundle) / variants[ratio]["file"]
        cover_input = empty_input if first_image else candidates(page, [".cover-editor-panel-select input[type=file][accept*=image]",
                                         ".cover-editor input[type=file][accept*=image]",
                                         ".cover-editor input[type=file][accept*='.jpg']"],
                                 visible=False, container_visible=True)
        if cover_input is None:
            raise ManualStep("未找到唯一的 B站封面图片上传控件。")
        cover_input.set_input_files(str(image), timeout=15000)
        error = None
        last_issue = None
        def rendered_correctly():
            nonlocal error, last_issue
            # A replacement image may update its zoom after it finishes loading.
            reset_bilibili_cover_zoom(page, (suffix.replace("_", "-"),))
            try:
                error = cover_canvas_error(pane, image)
                return error <= 6
            except Exception as exc:
                last_issue = str(exc)[:500]
                return False
        if not wait_for(page, rendered_correctly, 25, check):
            atomic_json(Path(bundle) / "bilibili_画布核对.json", {"state": "needs_manual", "ratio": ratio,
                        "pixel_rmse": error, "error": last_issue, "updated_at": dt_now()})
            raise ManualStep(f"{ratio} 封面画布与选图不一致，请手动核对，尚未保存。")
        verification[ratio] = {"pixel_rmse": round(error, 4), "sync_disabled": True, "zoom_reset": True}
    # Check both canvases again after the second upload, so a changed sync
    # control cannot silently replace the first ratio with the second image.
    for ratio, suffix in (("4:3", "4_3"), ("16:9", "16_9")):
        pane = editor.locator(f".cover-editor-panel-canvas > div:has(.scale-slider-{suffix.replace('_', '-')})")
        error = cover_canvas_error(pane, Path(bundle) / variants[ratio]["file"])
        if error > 6:
            raise ManualStep(f"{ratio} 封面在切换比例后发生变化，尚未保存，请手动检查。")
        verification[ratio]["pixel_rmse"] = round(error, 4)
    if not confirm_cover_dialog(page):
        raise ManualStep("双比例封面已填写，但未找到保存按钮。")
    if not wait_for(page, lambda: not cover_dialog_open(page), 30, check):
        raise ManualStep("封面保存窗口尚未关闭，请核对网页提示。")
    if not wait_for(page, lambda: bool(image_signature(page, settings.get("cover_preview", []))), 10, check):
        raise ManualStep("封面已保存，但主预览尚未出现。")
    atomic_json(Path(bundle) / "bilibili_画布核对.json", {"state": "passed", "checks": verification, "updated_at": dt_now()})
    return verification


def fill_upload(page, settings, data, cover, update, check=lambda: None, timeout=900):
    """Works with real DOMs and offline fixtures. Does not navigate or publish."""
    details = {"video_selected": False, "upload_complete": False,
               "title_filled": False, "description_filled": False, "cover_filled": False}
    if candidates(page, settings["video_input"], visible=False) is None:
        click_named(page, ("上传视频",))
    video_input = wait_for(page, lambda: candidates(page, settings["video_input"], visible=False), 30, check)
    if video_input is None:
        raise ManualStep("没有找到唯一的视频上传控件，请手动上传；网站结构可能已变化。")
    video_input.set_input_files(data["video"]["path"], timeout=30000)
    details["video_selected"] = True
    update("uploading", details, "视频已选择，等待编辑表单。")
    return fill_existing(page, settings, data, cover, update, check, timeout, details)


def fill_existing(page, settings, data, cover, update, check=lambda: None, timeout=900, details=None):
    details = dict(details or {"video_selected": True, "upload_complete": False,
                              "title_filled": False, "description_filled": False, "cover_filled": False})
    notes = []
    details.pop("cover_selected", None)
    host = urllib.parse.urlsplit(page.url).hostname or "browser"
    platform = {"member.bilibili.com": "bilibili", "creator.xiaohongshu.com": "xiaohongshu"}.get(host)
    control_root = Path(cover).parent
    if platform and (control_root / (platform + ".replacement.json")).exists():
        command = json.loads((control_root / (platform + ".replacement.json")).read_text(encoding="utf-8"))
        target = clean_path(command["bundle"])
        replacement = load_bundle(target, platform)
        if (replacement["video"]["sha256"] != data["video"]["sha256"]
                or clean_path(replacement["video"]["path"]) != clean_path(data["video"]["path"])):
            raise ManualStep("新发布包的成品与已上传视频不同，不能直接继续填写。")
        data = {**replacement, "fields": replacement["content"][platform]}
        cover = target / "cover.jpg"
        original_update = update
        def update(state, checks=None, message=""):
            original_update(state, checks, message)
            atomic_json(target / (platform + "_上传状态.json"), {"state": state, "checks": checks or {},
                        "message": message, "updated_at": dt_now(), "platform": platform, "final_publish": "human_only",
                        "control_bundle": str(control_root)})
    if platform:
        owner = {"pid": os.getpid(), "bundle": str(Path(cover).parent), "control_bundle": str(control_root)}
        atomic_json(Path(cover).parent / (platform + "_helper.json"), owner)
        atomic_json(ROOT / "cache/publish-browser" / platform / "active.json", owner)
    diagnostic_request = control_root / (host + ".diagnose")
    if diagnostic_request.exists():
        request_text = diagnostic_request.read_text(encoding="utf-8").strip()
        request = json.loads(request_text) if request_text else {}
        diagnostic_request.unlink()
        if request.get("open_cover") and not cover_dialog_open(page):
            if not open_cover(page, settings):
                raise ManualStep("没有找到封面编辑入口。")
            if not wait_for(page, lambda: cover_dialog_open(page), 10, check):
                raise ManualStep("封面编辑窗口未打开。")
        if request.get("reset_cover_zoom"):
            reset_bilibili_cover_zoom(page)
        if request.get("export_session") and platform:
            # Test-only handoff from this assistant's own authenticated profile.
            # The fixed private cache path is never included in diagnostics.
            state = page.context.storage_state()
            domains = ("bilibili.com", "biliapi.com") if platform == "bilibili" else ("xiaohongshu.com",)
            state["cookies"] = [c for c in state["cookies"] if any(c["domain"].lstrip(".") == d or c["domain"].endswith("." + d) for d in domains)]
            state["origins"] = [o for o in state["origins"] if any((urllib.parse.urlsplit(o["origin"]).hostname or "") == d or (urllib.parse.urlsplit(o["origin"]).hostname or "").endswith("." + d) for d in domains)]
            atomic_json(ROOT / "cache/publish-browser" / platform / "offscreen-session.json", state)
        atomic_json(Path(cover).parent / (host + "_页面诊断.json"), diagnose_page(page))
        return {"checks": details}
    title = wait_for(page, lambda: candidates(page, settings["title"]), min(timeout, 120), check)
    description = wait_for(page, lambda: candidates(page, settings["description"]), 20, check)
    if title is None or description is None:
        raise ManualStep("上传已开始，但未找到标题或简介表单。请在浏览器完成；不要重复上传。")
    fields = data["fields"]
    fill_exact(title, fields["title"])
    details["title_filled"] = True
    fill_exact(description, fields["description"])
    details["description_filled"] = True
    if platform and not details.get("upload_complete"):
        update("uploading", details, "标题和简介已写入，等待视频上传完成后设置封面。")
        details["upload_complete"] = bool(wait_for(page, lambda: upload_finished(page, settings), timeout, check))
        if not details["upload_complete"]:
            message = "未能确认上传完成，已保留填写内容；请查看网页进度，再继续填写。"
            update("needs_manual", details, message)
            return {"state": "needs_manual", "checks": details, "message": message}
    update("filling", details, "标题和简介已写入，处理封面。")
    before = image_signature(page, settings.get("cover_preview", []))
    cover_hash = file_hash(cover)
    variants = data.get("platform_covers", {}).get("bilibili") if platform == "bilibili" else None
    variant_hashes = {ratio: variants[ratio]["sha256"] for ratio in ("4:3", "16:9")} if variants else None
    already_saved = (not cover_dialog_open(page) and details.get("cover_filled")
                     and details.get("cover_sha256") == cover_hash
                     and (not variants or details.get("cover_variant_sha256") == variant_hashes)
                     and details.get("cover_signature") == before and bool(before))
    staged = (cover_dialog_open(page) and details.get("cover_pending", False)
              and details.get("pending_cover_sha256") == cover_hash)
    if not already_saved:
        details["cover_filled"] = False
    if details.get("pending_cover_sha256") != cover_hash:
        details["cover_pending"] = False
        details.pop("pending_cover_sha256", None)
    if variants and not already_saved:
        details["cover_pending"] = True
        details["pending_cover_sha256"] = cover_hash
        update("filling", details, "分别填写 B站4:3和16:9封面，保留完整原图并核对画布。")
        try:
            details["cover_crop_validation"] = upload_bilibili_covers(page, settings, Path(cover).parent, variants, check)
        except Exception as exc:
            update("needs_manual", details, str(exc)[:1200])
            raise
        details["cover_variant_sha256"] = variant_hashes
        details["cover_filled"] = True
        details["cover_sha256"] = cover_hash
        details["cover_signature"] = image_signature(page, settings.get("cover_preview", []))
        already_saved, staged = True, False
    cover_input = None if staged or already_saved else candidates(page, settings["cover_input"], visible=False, container_visible=True)
    if cover_input is None and not staged and not already_saved:
        open_cover(page, settings)
        def find_cover_input():
            found = candidates(page, settings["cover_input"], visible=False, container_visible=True)
            if found is None:
                if host == "creator.xiaohongshu.com":
                    clear = candidates(page, [".main-cover-editor-modal .uploaded-thumbnail-clear[aria-label='清除已上传封面']"])
                    if clear is not None and not clear.evaluate("e=>e.closest('button')?.disabled || false"):
                        modal_text = clear.evaluate("e=>e.closest('.main-cover-editor-modal').innerText")
                        if "设置封面" in modal_text and not re.search(r"发布|投稿|提交审核|提交作品", modal_text):
                            clear.click(timeout=7000)
                click_named(page, settings.get("cover_tabs", []))
                found = candidates(page, settings["cover_input"], visible=False, container_visible=True)
            return found
        cover_input = wait_for(page, find_cover_input, 15, check)
    if already_saved:
        details["cover_pending"] = False
        details.pop("pending_cover_sha256", None)
    elif cover_input is not None or staged:
        if cover_input is not None:
            cover_input.set_input_files(str(cover), timeout=15000)
        details["cover_pending"] = True
        details["pending_cover_sha256"] = cover_hash
        update("filling", details, "封面图片已选择，等待保存并核对预览。")
        confirmed = False
        def cover_ready():
            nonlocal confirmed
            if image_signature(page, settings.get("cover_preview", [])) != before and not cover_dialog_open(page):
                return True
            confirmed = confirm_cover_dialog(page) or confirmed
            return confirmed and not cover_dialog_open(page)
        wait_for(page, cover_ready, 30, check)
        changed = wait_for(page, lambda: (bool(image_signature(page, settings.get("cover_preview", [])))
                                         and (image_signature(page, settings.get("cover_preview", [])) != before
                                              or confirmed) and not cover_dialog_open(page)), 20, check)
        details["cover_filled"] = bool(changed)
        details["cover_pending"] = not bool(changed)
        if changed:
            details["cover_sha256"] = cover_hash
            details["cover_signature"] = image_signature(page, settings.get("cover_preview", []))
            details.pop("pending_cover_sha256", None)
        if not changed:
            host = urllib.parse.urlsplit(page.url).hostname or "browser"
            atomic_json(Path(cover).parent / (host + "_封面诊断.json"), diagnose_page(page))
            notes.append("封面已提交给上传控件，但预览尚未确认；请检查封面或手动完成裁剪。")
    else:
        host = urllib.parse.urlsplit(page.url).hostname or "browser"
        atomic_json(Path(cover).parent / (host + "_封面诊断.json"), diagnose_page(page))
        notes.append("没有找到唯一的封面上传控件，请手动选择发布包中的 cover.jpg。")
    update("uploading", details, "文字已填写，等待视频上传完成。")
    details["upload_complete"] = bool(wait_for(page, lambda: upload_finished(page, settings), timeout, check))
    if not details["upload_complete"]:
        notes.append("未能确认上传完成，请查看网页进度和转码状态。")
    required = ("video_selected", "upload_complete", "title_filled", "description_filled", "cover_filled")
    state = "waiting_review" if all(details.get(key) for key in required) else "needs_manual"
    message = "已上传填写，等待你在网页核对并手动发布。" if state == "waiting_review" else "；".join(notes)
    update(state, details, message)
    return {"state": state, "checks": details, "message": message}


def browser_engine_options(headless=False):
    # Keep cache and temporary files inside this application, and maintain a
    # separate login profile; never open/read the user's ordinary browser profile.
    temp = ROOT / "cache/browser-temp"
    temp.mkdir(parents=True, exist_ok=True)
    os.environ["TEMP"] = os.environ["TMP"] = str(temp)
    config = load_config()
    executable = config.get("publish_browser_executable")
    if executable:
        if not Path(executable).is_file():
            raise FileNotFoundError("助手内浏览器不存在，请恢复 dependencies/browser/chrome 目录。")
        return {"executable_path": executable, "headless": headless}
    return {"channel": config.get("publish_browser", "chrome"), "headless": headless}


def browser_options(headless=False):
    return {**browser_engine_options(headless),
            "viewport": {"width": 1440, "height": 960}, "locale": "zh-CN"}


def restore_migrated_session(context, profile):
    """One-time transfer of this assistant's existing dedicated login session."""
    path = Path(profile) / "browser-migration-session.json"
    if not path.is_file():
        return False
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("cookies"):
        context.add_cookies(state["cookies"])
    origins = json.dumps(state.get("origins", []), ensure_ascii=True)
    context.add_init_script("const vfSavedOrigins=" + origins + ";"
                            "for(const o of vfSavedOrigins){if(location.origin===o.origin){"
                            "for(const item of o.localStorage||[])localStorage.setItem(item.name,item.value);}}")
    path.unlink()
    return True


def run(platform, bundle=None, headless=False, login_only=False):
    from playwright.sync_api import sync_playwright
    from pipeline import job_lock
    settings = json.loads((ROOT / "publish-selectors.json").read_text(encoding="utf-8"))[platform]
    data = load_bundle(bundle, platform) if bundle is not None else None
    if not login_only and data is None:
        raise ValueError("请先生成发布资料包。")
    profile = ROOT / "cache/publish-browser" / platform
    profile.mkdir(parents=True, exist_ok=True)
    output = clean_path(bundle) if data else profile
    status_path = output / (platform + "_上传状态.json")
    stop_path = output / (platform + ".stop")
    inspect_path = output / (platform + ".inspect")
    repair_path = output / (platform + ".repair")
    with job_lock(profile):
        stop_path.unlink(missing_ok=True)
        atomic_json(output / (platform + "_helper.json"), {"pid": os.getpid(), "bundle": str(output)})
        atomic_json(profile / "active.json", {"pid": os.getpid(), "bundle": str(output)})

        def update(state, checks=None, message=""):
            if checks is None and status_path.exists():
                try:
                    checks = json.loads(status_path.read_text(encoding="utf-8")).get("checks", {})
                except (OSError, ValueError):
                    checks = {}
            atomic_json(status_path, {"state": state, "checks": checks or {}, "message": message,
                                      "updated_at": dt_now(), "platform": platform,
                                      "final_publish": "human_only"})
            print(message or state, flush=True)

        def check():
            if stop_path.exists():
                raise InterruptedError("已停止浏览器任务。")
            if inspect_path.exists():
                inspect_path.unlink()
                atomic_json(output / (platform + "_DOM诊断.json"), diagnose_page(page))
            try:
                settings.update(json.loads((ROOT / "publish-selectors.json").read_text(encoding="utf-8"))[platform])
            except (OSError, ValueError):
                pass

        with sync_playwright() as p:
            update("opening_browser", message="正在打开创作中心专用浏览器。")
            try:
                context = p.chromium.launch_persistent_context(str(profile), **browser_options(headless))
                restore_migrated_session(context, profile)
            except Exception as exc:
                update("failed", message="浏览器启动失败，请查看日志：" + str(exc)[:800])
                raise
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.set_default_timeout(8000)
                page.goto(settings["url"], wait_until="domcontentloaded", timeout=45000)
                update("waiting_login" if login_only else "opening_editor", message="请在打开的浏览器中登录；登录状态保存在助手的独立目录。" if login_only else "正在载入上传页；登录失效时请在专用浏览器重新登录。")
                if login_only:
                    while context.pages:
                        check()
                        try:
                            page.wait_for_timeout(500)
                        except Exception:
                            break
                    update("login_window_closed", message="登录窗口已关闭；后续上传会复用登录状态。")
                    return
                def editor_ready():
                    parsed = urllib.parse.urlsplit(page.url)
                    expected = "member.bilibili.com" if platform == "bilibili" else "creator.xiaohongshu.com"
                    if parsed.hostname != expected or "/login" in parsed.path:
                        return None
                    locator = candidates(page, settings["video_input"], visible=False)
                    if locator is None:
                        click_named(page, ("上传视频",))
                        locator = candidates(page, settings["video_input"], visible=False)
                    return locator
                found = wait_for(page, editor_ready, 600, check)
                if found is None:
                    raise ManualStep("等待登录或视频上传页超时，请登录后再次开始上传。")
                parsed = urllib.parse.urlsplit(page.url)
                expected = "member.bilibili.com" if platform == "bilibili" else "creator.xiaohongshu.com"
                if parsed.scheme != "https" or parsed.hostname != expected:
                    raise ManualStep("当前网页不是预期的创作中心，请检查浏览器地址。")
                fill_upload(page, settings, {**data, "fields": data["content"][platform]},
                            clean_path(bundle) / "cover.jpg", update, check)
            except InterruptedError as exc:
                update("cancelled", message=str(exc))
            except Exception as exc:
                update("needs_manual", message=str(exc)[:1200])
                if headless:
                    raise
            finally:
                if not headless and not stop_path.exists():
                    # Keep the review page alive. The human can edit/publish;
                    # closing the browser ends this process without an auto-save
                    # or auto-publish action.
                    while context.pages:
                        try:
                            check()
                            if data is not None and repair_path.exists():
                                repair_path.unlink()
                                import importlib
                                import publish_browser as adapter
                                adapter = importlib.reload(adapter)
                                prior = json.loads(status_path.read_text(encoding="utf-8")).get("checks", {})
                                if not prior.get("video_selected"):
                                    raise ManualStep("尚未选择视频，不能只继续填写。")
                                adapter.fill_existing(page, settings, {**data, "fields": data["content"][platform]},
                                                      clean_path(bundle) / "cover.jpg", update, check, details=prior)
                            context.pages[0].wait_for_timeout(500)
                        except InterruptedError:
                            break
                        except Exception as exc:
                            if not context.pages or page.is_closed():
                                break
                            update("needs_manual", message=str(exc)[:1200])
                context.close()
                (output / (platform + "_helper.json")).unlink(missing_ok=True)
                (profile / "active.json").unlink(missing_ok=True)


def dt_now():
    import datetime as dt
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds")


def diagnose_page(page):
    result = {"url": page.url, "captured_at": dt_now(), "frames": []}
    for frame in page.frames:
        if "member.bilibili.com" not in frame.url and "creator.xiaohongshu.com" not in frame.url:
            continue
        controls = frame.locator("input,textarea,[contenteditable],button,[role=dialog]").evaluate_all(
            "es => es.map(e=>({tag:e.tagName,type:e.type,class:e.className,placeholder:e.getAttribute('placeholder'),"
            "accept:e.accept,text:['BUTTON'].includes(e.tagName)||e.getAttribute('role')==='dialog'?e.innerText?.slice(0,300):undefined,"
            "parents:Array.from((function*(n){for(let i=0;n&&i<3;i++,n=n.parentElement)yield n})(e.parentElement))"
            ".map(p=>({tag:p.tagName,class:p.className,id:p.id,display:getComputedStyle(p).display,text:p.innerText?.slice(0,160)}))}))")
        result["frames"].append({"url": frame.url, "controls": controls,
                                 "scripts": frame.locator("script[src]").evaluate_all("es=>es.map(e=>e.src)"),
                                 "cover_actions": frame.locator(".cover-editor").get_by_text("完成", exact=True).evaluate_all(
                                     "es=>es.map(e=>({class:e.className,parents:Array.from((function*(n){for(let i=0;n&&i<3;i++,n=n.parentElement)yield n})(e.parentElement)).map(p=>({class:p.className,text:p.innerText?.slice(0,800),html:p.outerHTML.replace(/data:[^\\\"']+/g,'data:[omitted]').slice(0,5000)}))}))"),
                                 "cover_layout": frame.locator(".cover-editor canvas.lower-canvas").evaluate_all(
                                     "es=>es.map(e=>({parents:Array.from((function*(n){for(let i=0;n&&i<4;i++,n=n.parentElement)yield n})(e.parentElement)).map(p=>({class:p.className,text:p.innerText?.slice(0,1500),html:p.outerHTML.replace(/data:[^\\\"']+/g,'data:[omitted]').slice(0,10000)}))}))"),
                                 "canvas_pixels": frame.locator(".cover-editor canvas.lower-canvas").evaluate_all(
                                     "es=>es.map(e=>{try{return {editor:e.closest('.cover-editor-panel-canvas-image')?.id,image:e.toDataURL('image/png')}}catch{return {editor:e.closest('.cover-editor-panel-canvas-image')?.id,error:'canvas not readable'}}})"),
                                 "crop_controls": frame.locator(".cover-editor input,.cover-editor [role=slider],.cover-editor canvas,.cover-editor [class*=crop],.cover-editor [class*=scale]").evaluate_all(
                                     "es=>es.filter(e=>e.getBoundingClientRect().height>0).map(e=>({tag:e.tagName,class:e.className,type:e.type,min:e.min,max:e.max,step:e.step,value:e.value,style:e.getAttribute('style'),width:e.width,height:e.height,html:e.outerHTML.replace(/data:[^\\\"']+/g,'data:[omitted]').slice(0,2200)}))"),
                                 "dialogs": frame.locator("[role=dialog],.bcc-dialog,.bcc-dialog__wrap,.cover-editor,.el-dialog,.videoup-confirm-modal,.cover-modal").evaluate_all(
                                     "es=>es.filter(e=>e.getBoundingClientRect().height>0&&getComputedStyle(e).visibility!=='hidden').map(e=>({class:e.className,text:e.innerText?.slice(0,5000),html:e.outerHTML.replace(/data:[^\\\"']+/g,'data:[omitted]').slice(0,22000)}))"),
                                 "cover_markup": frame.locator(".cover-main,.publish-page-content-cover,.cover-plugin-preview").evaluate_all(
                                     "es=>es.filter(e=>e.getBoundingClientRect().height>0).map(e=>({class:e.className,html:e.outerHTML.replace(/data:[^\\\"']+/g,'data:[omitted]').slice(0,18000)}))"),
                                 "editor_roots": frame.locator(".bottom-content").evaluate_all(
                                     "es=>es.filter(e=>e.getBoundingClientRect().height>0).map(e=>({parents:Array.from((function*(n){for(let i=0;n&&i<6;i++,n=n.parentElement)yield n})(e.parentElement)).map(p=>({tag:p.tagName,class:p.className,text:p.innerText?.slice(0,1200)}))}))"),
                                 "cover_images": frame.locator(".cover-main,.cover-main .cover-img,.cover-preview,img[class*=cover]").evaluate_all(
                                     "es=>es.map(e=>({tag:e.tagName,class:e.className,src:e.src?.startsWith('data:')?'data:[omitted]':e.src,background:getComputedStyle(e).backgroundImage,complete:e.complete,width:e.naturalWidth}))"),
                                 "upload_and_cover": frame.locator("[class*='upload'],[class*='cover']").evaluate_all(
                                     "es => es.filter(e=>e.getBoundingClientRect().height>0).slice(0,80).map(e=>({tag:e.tagName,class:e.className,text:e.innerText?.slice(0,200)}))")})
    return result


def main():
    parser = argparse.ArgumentParser(description="上传视频、填写标题/简介/封面，最后人工发布")
    parser.add_argument("--platform", choices=("bilibili", "xiaohongshu"), required=True)
    parser.add_argument("--bundle")
    parser.add_argument("--login", action="store_true")
    args = parser.parse_args()
    if not args.login and not args.bundle:
        parser.error("需要 --bundle 发布包目录，或 --login")
    run(args.platform, args.bundle, login_only=args.login)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    main()
