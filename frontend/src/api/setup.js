import api from './index'

/** 步骤 1：自检。 */
export const getWelcome = () => api.get('/setup/welcome').then((r) => r.data)

/** 步骤 1.5：用 token 换 setup session。 */
export const verifyToken = (token) =>
  api.post('/setup/verify-token', { token }).then((r) => r.data)

/** 查询初始化状态（无鉴权）。 */
export const getStatus = () => api.get('/setup/status').then((r) => r.data)

/** 步骤 2：注册 ERP。 */
export const connectErp = (session, body) =>
  api.post('/setup/connect-erp', body, { headers: { 'X-Setup-Session': session } }).then((r) => r.data)

/** 步骤 3：创建第一个 admin。 */
export const createAdmin = (session, body) =>
  api.post('/setup/create-admin', body, { headers: { 'X-Setup-Session': session } }).then((r) => r.data)

/** 步骤 4：注册钉钉应用。 */
export const connectDingtalk = (session, body) =>
  api.post('/setup/connect-dingtalk', body, { headers: { 'X-Setup-Session': session } }).then((r) => r.data)

/** 步骤 5：注册 AI provider。 */
export const connectAi = (session, body) =>
  api.post('/setup/connect-ai', body, { headers: { 'X-Setup-Session': session } }).then((r) => r.data)

/** 步骤 6：完成。 */
export const setupComplete = (session) =>
  api.post('/setup/complete', {}, { headers: { 'X-Setup-Session': session } }).then((r) => r.data)
