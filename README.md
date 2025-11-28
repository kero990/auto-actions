# actions自动编译脚本
这里是我个人用actions编译程序的CI仓库，大部分是为UOS/deepin编译的deb，主要是为了适配较低的glibc2.28而重编译各种应用。因为主流的开源release都使用ubuntu20-22来编译，无法在UOS/deepinV20运行。
其中大部分使用debian:10容器编译，少部分使用rocky8，因为rocky8同样使用glibc2.28，但拥有gcc-toolset-9-14，可以完整支持C++20
