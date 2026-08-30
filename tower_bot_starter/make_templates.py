
from pathlib import Path
from PIL import Image
import argparse


def main() -> None:
    parser = argparse.ArgumentParser(
        description="从三张 550×1020 参考截图重新裁剪识别模板。"
    )
    parser.add_argument("select_screen", help="难度选择界面截图")
    parser.add_argument("confirm_screen", help="开始挑战弹窗截图")
    parser.add_argument("result_screen", help="战斗结算界面截图")
    args = parser.parse_args()

    output_dir = Path(__file__).resolve().parent / "templates"
    output_dir.mkdir(exist_ok=True)

    select_image = Image.open(args.select_screen).convert("RGB")
    confirm_image = Image.open(args.confirm_screen).convert("RGB")
    result_image = Image.open(args.result_screen).convert("RGB")

    # 坐标来自本项目配套的 550×1020 参考图。
    select_image.crop((180, 875, 370, 925)).save(
        output_dir / "challenge.png"
    )
    confirm_image.crop((205, 675, 350, 740)).save(
        output_dir / "start.png"
    )
    result_image.crop((220, 748, 335, 785)).save(
        output_dir / "close.png"
    )

    print(f"模板已生成：{output_dir}")


if __name__ == "__main__":
    main()
