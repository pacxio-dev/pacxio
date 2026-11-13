"""python -m pacxio ... 等价于 pacxio ...（没有把命令装进 PATH 时用）。"""

from pacxio.cli import main

if __name__ == "__main__":
    main()
