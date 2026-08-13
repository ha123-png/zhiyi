import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


WIDTH = 1600
HEIGHT = 1100


def _font(size: int) -> ImageFont.FreeTypeFont:
    candidates = (
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/simsun.ttc"),
    )
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    raise RuntimeError("生成中文冒烟样本需要微软雅黑、黑体或宋体字体")


def _base(title: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (WIDTH, HEIGHT), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((35, 35, WIDTH - 35, HEIGHT - 35), outline="#333333", width=3)
    draw.text((WIDTH // 2, 78), title, font=_font(52), anchor="ma", fill="#111111")
    return image, draw


def _line(draw: ImageDraw.ImageDraw, y: int) -> None:
    draw.line((70, y, WIDTH - 70, y), fill="#777777", width=2)


def _invoice(
    *,
    seller: str,
    buyer: str,
    number: str,
    date: str,
    total: float,
    items: list[tuple[str, int, float]],
) -> Image.Image:
    image, draw = _base("增值税电子普通发票（合成冒烟样本）")
    body = _font(30)
    small = _font(25)
    draw.text((90, 175), f"发票号码：{number}", font=body, fill="#111111")
    draw.text((1040, 175), f"开票日期：{date}", font=body, fill="#111111")
    _line(draw, 230)
    draw.text((90, 270), f"购买方名称：{buyer}", font=body, fill="#111111")
    draw.text((90, 330), f"销售方名称：{seller}", font=body, fill="#111111")
    _line(draw, 395)
    headers = ("项目名称", "规格", "数量", "单价", "金额")
    xs = (90, 610, 900, 1080, 1310)
    for x, header in zip(xs, headers, strict=True):
        draw.text((x, 430), header, font=small, fill="#222222")
    y = 495
    for index, (name, quantity, price) in enumerate(items, start=1):
        draw.text((90, y), f"{index}. {name}", font=small, fill="#111111")
        draw.text((610, y), "标准", font=small, fill="#111111")
        draw.text((900, y), str(quantity), font=small, fill="#111111")
        draw.text((1080, y), f"{price:.2f}", font=small, fill="#111111")
        draw.text((1310, y), f"{quantity * price:.2f}", font=small, fill="#111111")
        y += 62
    _line(draw, 850)
    draw.text((1030, 900), f"价税合计（小写）：￥{total:.2f}", font=body, fill="#111111")
    draw.text((90, 995), "说明：本文件完全由项目脚本生成，不含真实企业数据。", font=small, fill="#555555")
    return image


def _delivery(
    *,
    seller: str,
    buyer: str,
    number: str,
    date: str,
    items: list[tuple[str, str, int, float]],
) -> Image.Image:
    image, draw = _base("送货单（合成冒烟样本）")
    body = _font(30)
    small = _font(24)
    draw.text((90, 175), f"供应商：{seller}", font=body, fill="#111111")
    draw.text((90, 235), f"客户：{buyer}", font=body, fill="#111111")
    draw.text((920, 175), f"单号：{number}", font=body, fill="#111111")
    draw.text((920, 235), f"日期：{date}", font=body, fill="#111111")
    _line(draw, 310)
    headers = ("序号", "商品名称", "规格", "数量", "单价", "金额")
    xs = (80, 190, 680, 930, 1120, 1340)
    for x, header in zip(xs, headers, strict=True):
        draw.text((x, 350), header, font=small, fill="#222222")
    total = 0.0
    y = 415
    for index, (name, spec, quantity, price) in enumerate(items, start=1):
        amount = quantity * price
        total += amount
        values = (str(index), name, spec, str(quantity), f"{price:.2f}", f"{amount:.2f}")
        for x, value in zip(xs, values, strict=True):
            draw.text((x, y), value, font=small, fill="#111111")
        y += 62
    _line(draw, 850)
    draw.text((1220, 900), f"合计：￥{total:.2f}", font=body, fill="#111111")
    draw.text((90, 995), "说明：本文件完全由项目脚本生成，不含真实企业数据。", font=small, fill="#555555")
    return image


def _save(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG", optimize=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成不含真实业务数据的模型冒烟样本")
    parser.add_argument("--output", type=Path, default=Path("数据集/合成冒烟"))
    args = parser.parse_args()

    invoice_clean = _invoice(
        seller="青禾办公用品有限公司",
        buyer="远山信息技术有限公司",
        number="SYN-INV-20260807-001",
        date="2026年08月07日",
        total=226.0,
        items=[("打印纸", 2, 58.0), ("文件夹", 10, 11.0)],
    )
    invoice_rotated = _invoice(
        seller="海风设备服务有限公司",
        buyer="星河商贸有限公司",
        number="SYN-INV-20260807-002",
        date="2026年08月06日",
        total=1280.0,
        items=[("设备巡检服务", 1, 1280.0)],
    ).rotate(1.2, resample=Image.Resampling.BICUBIC, expand=False, fillcolor="white")
    delivery_long = _delivery(
        seller="长桥供应链有限公司",
        buyer="晨光零售有限公司",
        number="SYN-DEL-20260807-001",
        date="2026年08月07日",
        items=[
            ("矿泉水", "550ml×24", 3, 36.0),
            ("抽纸", "3层×100抽", 8, 12.5),
            ("洗手液", "500ml", 5, 18.0),
            ("垃圾袋", "45cm×50cm", 6, 9.0),
            ("记号笔", "黑色", 12, 3.5),
            ("封箱胶带", "透明60mm", 4, 8.0),
        ],
    )
    delivery_blurred = _delivery(
        seller="北辰食品配送有限公司",
        buyer="四季餐饮管理有限公司",
        number="SYN-DEL-20260807-002",
        date="2026年08月05日",
        items=[("大米", "25kg", 4, 138.0), ("食用油", "5L", 6, 72.0)],
    ).filter(ImageFilter.GaussianBlur(radius=0.75))

    files = {
        "发票/synthetic-invoice-clean.png": invoice_clean,
        "发票/synthetic-invoice-rotated.png": invoice_rotated,
        "送货单/synthetic-delivery-long.png": delivery_long,
        "送货单/synthetic-delivery-blurred.png": delivery_blurred,
    }
    for relative_path, image in files.items():
        _save(image, args.output / relative_path)

    golden = {
        "schema_version": 2,
        "review_status": "synthetic_known_values",
        "samples": {
            "synthetic-invoice-clean.png": {
                "category": "发票",
                "source_kind": "synthetic",
                "fields": {
                    "seller_name": "青禾办公用品有限公司",
                    "buyer_name": "远山信息技术有限公司",
                    "document_number": "SYN-INV-20260807-001",
                    "document_date": "2026年08月07日",
                    "total_amount": 226.0,
                },
                "item_count": 2,
            },
            "synthetic-invoice-rotated.png": {
                "category": "发票",
                "source_kind": "synthetic",
                "fields": {
                    "seller_name": "海风设备服务有限公司",
                    "buyer_name": "星河商贸有限公司",
                    "document_number": "SYN-INV-20260807-002",
                    "document_date": "2026年08月06日",
                    "total_amount": 1280.0,
                },
                "item_count": 1,
            },
            "synthetic-delivery-long.png": {
                "category": "送货单",
                "source_kind": "synthetic",
                "fields": {
                    "seller_name": "长桥供应链有限公司",
                    "buyer_name": "晨光零售有限公司",
                    "document_number": "SYN-DEL-20260807-001",
                    "document_date": "2026年08月07日",
                    "total_amount": 426.0,
                },
                "item_count": 6,
            },
            "synthetic-delivery-blurred.png": {
                "category": "送货单",
                "source_kind": "synthetic",
                "fields": {
                    "seller_name": "北辰食品配送有限公司",
                    "buyer_name": "四季餐饮管理有限公司",
                    "document_number": "SYN-DEL-20260807-002",
                    "document_date": "2026年08月05日",
                    "total_amount": 984.0,
                },
                "item_count": 2,
            },
        },
    }
    (args.output / "golden.json").write_text(
        json.dumps(golden, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"generated {len(files)} synthetic smoke samples in {args.output}")


if __name__ == "__main__":
    main()
