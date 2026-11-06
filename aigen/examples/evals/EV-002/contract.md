## 契约节选：POST /resources

这里只列契约里真正有的状态码。样本用例如果引用了下面没有的码，就是编造出处。

- 201：`{id: string, name: string}`
- 400：`{error: string}`

## 需求节选

- AC-03：名称不合法时拒绝创建，并指出名称字段。
