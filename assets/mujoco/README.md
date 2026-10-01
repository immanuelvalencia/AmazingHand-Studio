# Bundled AmazingHand models

These files are copied without modification from Pollen Robotics' AmazingHand project:
https://github.com/pollen-robotics/AmazingHand

Source directory: `Demo/AHSimulation/AHSimulation/` in the locally supplied upstream checkout. Its commit identifier was not available during packaging.

`AH_Left/mjcf/scene.xml` and `AH_Right/mjcf/scene.xml` include all referenced XML and STL assets. No sibling checkout or model download is required. Only scene dependencies are included; unused CAD files and demo applications are omitted.

## Attribution and licenses

Credit: Pollen Robotics and the AmazingHand contributors. The upstream README credits Steve N'Guyen for the MuJoCo/Mink demo.

The upstream project declares Apache License 2.0; its complete license is preserved in `LICENSE-Apache-2.0.txt`.
The upstream README separately licenses the mechanical design under Creative Commons Attribution 4.0 International (CC BY 4.0):
https://creativecommons.org/licenses/by/4.0/
This mechanical-design attribution applies to the bundled hand meshes. No changes were made to the copied model or mesh files.

These notices describe the upstream assets, not a license selection for the rest of AmazingHand Studio.
