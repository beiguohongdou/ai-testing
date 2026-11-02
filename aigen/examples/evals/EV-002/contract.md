## OpenAPI 节选：POST /users/login

- 200：`{access_token: string, token_type: string, expires_in: integer}`
- 401：`{error: "Unauthorized"}`

## PRD 节选

- AC-03：密码错误时提示「账号或密码错误」，不提示账号是否存在。
