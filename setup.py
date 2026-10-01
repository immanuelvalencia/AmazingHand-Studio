"""Install the public control library: python -m pip install -e ."""
from pathlib import Path
from setuptools import setup, find_packages
root = Path(__file__).resolve().parent
setup(
    name="amazinghand-studio", version="0.1.0",
    description="Python control library for AmazingHand Studio",
    url="https://github.com/immanuelvalencia/AmazingHand-Studio",
    license="Apache-2.0",
    license_files=["LICENSE", "NOTICE"],
    packages=find_packages(".", include=["amazinghand", "amazinghand.*"]),
    package_data={"amazinghand": ["python_guide.md"]},
    python_requires=">=3.11",
    install_requires=[line.strip() for line in (root / "requirements.txt").read_text(encoding="utf-8").splitlines()
                      if line.strip() and not line.startswith("#")],
)
