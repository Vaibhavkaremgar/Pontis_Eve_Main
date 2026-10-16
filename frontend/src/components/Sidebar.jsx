import React from "react";
import {
  Briefcase,
  BookmarkCheck,
  User,
  FileText,
  Zap,
  PanelLeft,
  ChevronsUpDown,
  Star,
  LogOut,
  Bell,
  CreditCard,
  Settings,
} from "lucide-react";

export default function Sidebar({
  activeTab,
  setActiveTab,
  userProfile,
  footerIdentity,
  jobsCount,
  opportunitiesCount,
  onLogout,
  onSettings,
  onGuidanceClick,
  collapsed = false,
  onToggleCollapsed,
}) {
  const displayName = (footerIdentity ?? userProfile).name;
  const displayEmail = (footerIdentity ?? userProfile).email;
  const profileGuidance = userProfile?.profile_strength_detail?.ninety_percent_guidance;
  const authoritativeMissing = userProfile?.missing_questions || [];
  const guidanceItems = authoritativeMissing.map((item) => ({
    ...item,
    section: item.topic_id,
    action: item.question,
  }));
  const [menuOpen, setMenuOpen] = React.useState(false);
  const menuRef = React.useRef(null);

  React.useEffect(() => {
    if (!menuOpen) return;
    function handleClick(e) {
      if (menuRef.current && !menuRef.current.contains(e.target)) setMenuOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [menuOpen]);

  React.useEffect(() => {
    if (collapsed) setMenuOpen(false);
  }, [collapsed]);
  const tabs = [
    { id: "jobs", label: "New jobs", icon: Briefcase, badge: jobsCount },
    { id: "tracked", label: "Tracked jobs", icon: BookmarkCheck },
    { id: "profile", label: "Profile", icon: User },
    { id: "documents", label: "Documents", icon: FileText },
    { id: "opportunities", label: "Notifications", icon: Bell, badge: opportunitiesCount || 0 },
    { id: "billing", label: "Billing", icon: CreditCard },
  ];

  return (
    <aside
      data-testid="left-sidebar"
      data-collapsed={collapsed ? "true" : "false"}
      className="h-full w-full min-w-0 flex flex-col justify-between bg-[#FBFBF9] text-[#1F1F1F]"
    >
      <div className="flex-1 overflow-y-auto eve-scroll">
        {/* Brand */}
        <div className={`flex items-center pt-4 pb-3 ${collapsed ? "justify-center px-2" : "justify-between px-4"}`}>
          <div
            aria-label="Pontis"
            role="img"
            className={`h-10 overflow-hidden rounded-md bg-[#1F1F1F] bg-no-repeat ${collapsed ? "w-9" : "w-[136px]"}`}
            style={{
              backgroundImage: "url('/pontis.one.jpeg')",
              backgroundSize: collapsed ? "154px auto" : "231px auto",
              backgroundPosition: collapsed ? "center 43%" : "center 43%",
            }}
          />
          <button
            type="button"
            data-testid="sidebar-collapse-btn"
            onClick={onToggleCollapsed}
            className="flex h-8 w-8 items-center justify-center rounded-md text-[#9A9A98] hover:bg-black/[0.03] hover:text-[#1F1F1F] transition-colors"
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            aria-expanded={!collapsed}
          >
            <PanelLeft className="w-4 h-4" strokeWidth={1.5} />
          </button>
        </div>

        {/* Navigation */}
        <nav className={`space-y-0.5 ${collapsed ? "px-1" : "px-2"}`} data-testid="sidebar-nav">
          {tabs.map((t) => {
            const Icon = t.icon;
            const active = activeTab === t.id;
            return (
              <button
                key={t.id}
                data-testid={`nav-tab-${t.id}`}
                onClick={() => setActiveTab(t.id)}
                aria-label={collapsed ? t.label : undefined}
                title={collapsed ? t.label : undefined}
                className={`w-full flex items-center rounded-lg text-[13px] transition-colors ${collapsed ? "justify-center px-1 py-2" : "justify-between gap-2 px-3 py-2"} ${
                  active
                    ? "bg-black/[0.05] text-[#1F1F1F] font-medium"
                    : "text-[#4A4A48] hover:bg-black/[0.03] font-normal"
                }`}
              >
                <span className={`flex items-center ${collapsed ? "justify-center" : "gap-2.5"}`}>
                  <Icon
                    className={`w-[15px] h-[15px] ${
                      active ? "text-[#1F1F1F]" : "text-[#7A7A78]"
                    }`}
                    strokeWidth={1.5}
                  />
                  {!collapsed && <span>{t.label}</span>}
                </span>
                {!collapsed && t.badge ? (
                  <span
                    data-testid={`nav-badge-${t.id}`}
                    className="min-w-[18px] h-[18px] px-1.5 rounded-full bg-[#1F1F1F] text-white text-[10px] font-medium flex items-center justify-center"
                  >
                    {t.badge}
                  </span>
                ) : null}
              </button>
            );
          })}
        {/*}
          <button
            data-testid="nav-tab-coaching"
            className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-[13px] text-[#4A4A48] hover:bg-black/[0.03] transition-colors font-normal"
          >
            <Zap
              className="w-[15px] h-[15px] text-[#C58B3E]"
              strokeWidth={1.5}
              fill="#C58B3E"
            />
            <span>Coaching</span>
          </button>
          */}
        </nav>

        {!collapsed && guidanceItems.length > 0 && (
          <section
            data-testid="profile-90-guidance"
            className="mx-2 mt-3 min-w-0 rounded-2xl border border-[#DDD8EF] bg-[#F7F5FC] p-4"
          >
            <p className="text-[14px] font-semibold text-[#1F1F1F]">
              {profileGuidance && profileGuidance.current_percent < 90
                ? "Reach 90% Profile"
                : "Complete your profile"}
            </p>
            <p className="mt-1 text-[12.5px] text-[#4A4A48]">
              {profileGuidance
                ? `${profileGuidance.current_percent}% complete · ${profileGuidance.remaining_percent_to_90}% to go`
                : `${guidanceItems.length} missing detail${guidanceItems.length === 1 ? "" : "s"}`}
            </p>
            {guidanceItems.length > 0 && (
              <ul className="mt-3 space-y-2">
                {guidanceItems.map((item) => (
                  <li key={`${item.section}-${item.action}`} className="min-w-0">
                    <button
                      type="button"
                      data-testid={`profile-guidance-${item.section}`}
                      onClick={() => onGuidanceClick?.(item)}
                      className="block w-full break-words text-left text-[12.5px] text-[#62578F] underline underline-offset-2 hover:text-[#4F437F]"
                    >
                      {item.action}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}
        

        {/* Recent activity */}
        {/*}
        <div className="px-3 pt-6">
          <p className="px-2 text-[11px] font-normal text-[#9A9A98] mb-2">
            Recent activity
          </p>
          <ul className="space-y-0.5">
            {recentActivity.map((r) => (
              <li key={r.id}>
                <button
                  className="w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-[12px] text-[#4A4A48] hover:bg-black/[0.03] transition-colors text-left"
                  data-testid={`recent-activity-${r.id}`}
                >
                  <Star
                    className="w-[13px] h-[13px] text-[#B5B5B3] shrink-0"
                    strokeWidth={1.5}
                  />
                  <span className="truncate">
                    <span className="font-normal text-[#1F1F1F]">
                      {r.company}
                    </span>
                    <span className="text-[#9A9A98]"> · {r.role}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
        */}
      </div>
       
      {/* User footer */}
      <div className={`${collapsed ? "px-1" : "px-3"} py-3 border-t border-black/[0.06]`} ref={menuRef}>
        {menuOpen && (
          <div className="mb-1 rounded-lg border border-black/[0.07] bg-white shadow-sm overflow-hidden">
            <button
              data-testid="settings-btn"
              onClick={() => {
                setMenuOpen(false);
                onSettings?.();
              }}
              className="w-full flex items-center gap-2 px-3 py-2 text-[13px] text-[#1F1F1F] hover:bg-black/[0.04] transition-colors"
            >
              <Settings className="w-[14px] h-[14px] text-[#7A7A78]" strokeWidth={1.5} />
              Settings
            </button>
            <div className="h-px bg-black/[0.06]" />
            <button
              data-testid="logout-btn"
              onClick={onLogout}
              className="w-full flex items-center gap-2 px-3 py-2 text-[13px] text-[#1F1F1F] hover:bg-black/[0.04] transition-colors"
            >
              <LogOut className="w-[14px] h-[14px] text-[#7A7A78]" strokeWidth={1.5} />
              Logout
            </button>
          </div>
        )}
        <button
          data-testid="user-profile-card"
          onClick={() => setMenuOpen((v) => !v)}
          aria-label={collapsed ? "Open account menu" : undefined}
          title={collapsed ? "Account" : undefined}
          className={`w-full flex items-center rounded-lg hover:bg-black/[0.03] transition-colors ${collapsed ? "justify-center px-1 py-1.5" : "gap-2.5 px-2 py-1.5"}`}
        >
          <div
            data-testid="user-profile-icon"
            className="w-8 h-8 rounded-full bg-[#E7E3F0] flex items-center justify-center shrink-0"
            aria-hidden="true"
          >
            <User className="w-[15px] h-[15px] text-[#7B6FB8]" strokeWidth={1.8} />
          </div>
          {!collapsed && <div className="flex-1 min-w-0 text-left">
            <p data-testid="sidebar-footer-name" className="text-[12px] font-medium text-[#1F1F1F] truncate">
              {displayName}
            </p>
            <p data-testid="sidebar-footer-email" className="text-[10.5px] text-[#9A9A98] truncate">
              {displayEmail}
            </p>
          </div>}
          {!collapsed && <ChevronsUpDown
            className="w-3.5 h-3.5 text-[#9A9A98] shrink-0"
            strokeWidth={1.5}
          />}
        </button>
      </div>
    </aside>
  );
}
