# Tiny Greeting

一个最小的 C 示例项目：编译后标准输出一行问候语。

## 依赖

- C 编译器（`cc` 或 `gcc`）
- GNU Make

## 构建

```sh
make
```

构建成功后在当前目录生成可执行文件 `hello`。

## 运行

```sh
./hello
```

预期输出：

```text
hello E3
```

## 清理

```sh
make clean
```

删除构建产物 `hello` 与 `main.o`。