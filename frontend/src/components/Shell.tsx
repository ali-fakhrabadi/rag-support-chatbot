import type { ReactNode } from "react";
import { NavLink } from "react-router";
import { useSession } from "../App";

export function Logo() {
  return (
    <svg className="logo" viewBox="0 0 64 64" aria-hidden="true">
      <rect width="64" height="64" rx="14" fill="#0B1424" />
      <path
        d="M32 12 48 18v12c0 11-7 19-16 22-9-3-16-11-16-22V18z"
        fill="none"
        stroke="#2DD4BF"
        strokeWidth="4"
        strokeLinejoin="round"
      />
      <path d="M25 31h14M25 38h9" stroke="#60A5FA" strokeWidth="4" strokeLinecap="round" />
    </svg>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const { me, signOut } = useSession();
  return (
    <div className="shell">
      <aside className="sidebar" aria-label="Main navigation">
        <div className="brand">
          <Logo />
          <div>
            <strong>Knowledge Assistant</strong>
            <span>{me?.tenant.name}</span>
          </div>
        </div>
        <nav>
          <NavLink to="/chat">Ask</NavLink>
          <NavLink to="/documents">Documents</NavLink>
        </nav>
        <div className="sidebar-footer">
          <div className="who">
            <span className="avatar" aria-hidden="true">
              {(me?.full_name || me?.email || "?").slice(0, 1).toUpperCase()}
            </span>
            <div>
              <div className="who-name">{me?.full_name || me?.email}</div>
              <div className="who-role">{me?.role === "tenant_admin" ? "Admin" : "Member"}</div>
            </div>
          </div>
          <button type="button" className="link-button" onClick={() => void signOut()}>
            Sign out
          </button>
        </div>
      </aside>
      <main className="content">{children}</main>
    </div>
  );
}
