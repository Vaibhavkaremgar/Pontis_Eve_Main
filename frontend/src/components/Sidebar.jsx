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
  recentActivity,
  onLogout,
  onSettings,
}) {
  const displayName = (footerIdentity ?? userProfile).name;
  const displayEmail = (footerIdentity ?? userProfile).email;
  const profileGuidance = userProfile?.profile_strength_detail?.ninety_percent_guidance;
  const guidanceItems = profileGuidance?.items || [];
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
      className="h-full w-full flex flex-col justify-between bg-[#FBFBF9] text-[#1F1F1F]"
    >
      <div className="flex-1 overflow-y-auto eve-scroll">
        {/* Brand */}
        <div className="flex items-center justify-between px-4 pt-5 pb-4">
          <span className="text-[15px] font-medium tracking-tight text-[#1F1F1F]">
            Eve
          </span>
          <button
            data-testid="sidebar-collapse-btn"
            className="text-[#9A9A98] hover:text-[#1F1F1F] transition-colors"
            aria-label="Collapse sidebar"
          >
            <PanelLeft className="w-4 h-4" strokeWidth={1.5} />
          </button>
        </div>

        {/* Navigation */}
        <nav className="px-2 space-y-0.5" data-testid="sidebar-nav">
          {tabs.map((t) => {
            const Icon = t.icon;
            const active = activeTab === t.id;
            return (
              <button
                key={t.id}
                data-testid={`nav-tab-${t.id}`}
                onClick={() => setActiveTab(t.id)}
                className={`w-full flex items-center justify-between gap-2 px-3 py-2 rounded-lg text-[13px] transition-colors ${
                  active
                    ? "bg-black/[0.05] text-[#1F1F1F] font-medium"
                    : "text-[#4A4A48] hover:bg-black/[0.03] font-normal"
                }`}
              >
                <span className="flex items-center gap-2.5">
                  <Icon
                    className={`w-[15px] h-[15px] ${
                      active ? "text-[#1F1F1F]" : "text-[#7A7A78]"
                    }`}
                    strokeWidth={1.5}
                  />
                  <span>{t.label}</span>
                </span>
                {t.badge ? (
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

        {profileGuidance && profileGuidance.current_percent < 90 && (
          <section
            data-testid="profile-90-guidance"
            className="mx-2 mt-3 min-w-0 rounded-2xl border border-[#DDD8EF] bg-[#F7F5FC] p-4"
          >
            <p className="text-[14px] font-semibold text-[#1F1F1F]">
              Reach 90% Profile
            </p>
            <p className="mt-1 text-[12.5px] text-[#4A4A48]">
              {profileGuidance.current_percent}% complete · {profileGuidance.remaining_percent_to_90}% to go
            </p>
            {guidanceItems.length > 0 && (
              <ul className="mt-3 space-y-2">
                {guidanceItems.map((item) => (
                  <li key={`${item.section}-${item.action}`} className="min-w-0">
                    <a
                      data-testid={`profile-guidance-${item.section}`}
                      href={`#profile-${item.section}`}
                      onClick={() => setActiveTab("profile")}
                      className="block break-words text-[12.5px] text-[#62578F] underline underline-offset-2 hover:text-[#4F437F]"
                    >
                      {item.action}
                    </a>
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
      <div className="px-3 py-3 border-t border-black/[0.06]" ref={menuRef}>
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
          className="w-full flex items-center gap-2.5 px-2 py-1.5 rounded-lg hover:bg-black/[0.03] transition-colors"
        >
          <div
            data-testid="user-profile-icon"
            className="w-8 h-8 rounded-full bg-[#E7E3F0] flex items-center justify-center shrink-0"
            aria-hidden="true"
          >
            <User className="w-[15px] h-[15px] text-[#7B6FB8]" strokeWidth={1.8} />
          </div>
          <div className="flex-1 min-w-0 text-left">
            <p data-testid="sidebar-footer-name" className="text-[12px] font-medium text-[#1F1F1F] truncate">
              {displayName}
            </p>
            <p data-testid="sidebar-footer-email" className="text-[10.5px] text-[#9A9A98] truncate">
              {displayEmail}
            </p>
          </div>
          <ChevronsUpDown
            className="w-3.5 h-3.5 text-[#9A9A98] shrink-0"
            strokeWidth={1.5}
          />
        </button>
      </div>
    </aside>
  );
}
