import { useEffect, useState, useSyncExternalStore } from "react";
import { Modal } from "antd";
import type Keycloak from "keycloak-js";
import { browserIdentity, loginReturnUrl, getAuthenticationState, subscribeAuthentication } from "./identity";

/** Native identity pages: no passwords or privileged admin credentials in P3. */
export default function IdentityActions() {
  const authenticated = useSyncExternalStore(subscribeAuthentication, getAuthenticationState);
  const [client, setClient] = useState<Keycloak | null>(null);
  const [error, setError] = useState("");
  const [invitationOpen, setInvitationOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let cancelled = false;
    browserIdentity().then(adapter => {
      if (!cancelled) { setClient(adapter); if (!adapter) setError("当前部署未开启账号服务，请使用监测台配置连接。"); }
    }).catch(() => { if (!cancelled) setError("账号服务暂不可用，请检查 P3 和身份服务。"); });
    return () => { cancelled = true; };
  }, []);

  async function navigate(action: "login" | "register" | "account") {
    if (!client || busy) return;
    setBusy(true); setError("");
    try {
      if (action === "register") await client.register({ redirectUri: loginReturnUrl() });
      else if (action === "account") await client.accountManagement();
      else await client.login({ redirectUri: loginReturnUrl(), prompt: "login" });
    } catch { setError("无法打开账号页面，请稍后重试。"); }
    finally { setBusy(false); }
  }
  const adminUrl = client?.authServerUrl && client.realm
    ? client.authServerUrl.replace(/\/$/, "") + "/admin/master/console/#/" + encodeURIComponent(client.realm) + "/organizations"
    : undefined;

  return <>
    <nav className="identity-actions" aria-label="账号与组织">
      {!authenticated && (client || error) && <button className="identity-register" disabled={!client || busy} onClick={() => void navigate("register")}>注册账号</button>}
      <button className="identity-signin" disabled={!client || busy} onClick={() => void navigate("login")}>{authenticated ? "切换账号" : "登录 / 切换账号"}</button>
      <button className="identity-invite" onClick={() => setInvitationOpen(true)}>邀请成员</button>
      <button className="identity-account" disabled={!client || busy} onClick={() => void navigate("account")}>个人账号</button>
    </nav>
    {error && <div className="identity-entry-error" role="alert">{error}</div>}
    <Modal title="邀请成员加入组织" open={invitationOpen} onCancel={() => setInvitationOpen(false)} footer={null} width={560} className="identity-entry-modal">
      <p className="identity-intro">通过 Keycloak 原生邀请，为新成员开通组织访问。</p>
      <div className="identity-invite-note"><strong>当前由平台管理员发起邀请</strong><p>P3 的“组织管理员”不等于平台管理员。此入口不会为当前账号增加管理权限。</p></div>
      <ol className="identity-invite-steps">
        <li><strong>选择要加入的组织</strong><span>打开组织后台，选择组织 A 或 B，进入“成员 / Members”。</span></li>
        <li><strong>发送邮件邀请</strong><span>点击“邀请成员 / Invite member”，填写新成员邮箱。成员通过邮件接受邀请。</span></li>
        <li><strong>分配组织角色，再登录 P3</strong><span>管理员在该组织中分配普通成员或只读成员分组；仅注册或接受邀请不会自动获得 P3 权限。</span></li>
      </ol>
      <p className="identity-invite-footnote">若已登录的演示账号没有管理权限，请在独立浏览器会话中使用平台管理员账号。新用户尚未分组时，P3 显示未授权是正常情况。</p>
      {adminUrl ? <a className="identity-admin-link" href={adminUrl} rel="noopener noreferrer">打开组织后台，发起邀请 →</a> : <p role="status">组织后台地址暂不可用，请恢复身份服务后重试。</p>}
    </Modal>
  </>;
}
