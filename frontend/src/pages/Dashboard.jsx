import React from "react";
import axios from "axios";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { Toaster, toast } from "sonner";
import { Bell } from "lucide-react";

import Sidebar from "../components/Sidebar";
import ChatHub from "../components/ChatHub";
import LivingProfile from "../components/LivingProfile";
import SwipeJobDeck from "../components/SwipeJobCard";
import CandidateSettingsModal from "../components/CandidateSettingsModal";
import { MOCK_RECENT_ACTIVITY } from "../mock";
import { getDynamicChatSuggestions } from "../lib/chatSuggestions";
import {
  isVoiceIntakeCompleteStatus,
  loadOnboardingState,
  saveOnboardingState,
  clearOnboardingState,
} from "../lib/onboardingStorage";
import { buildDashboardEveGreetingFromProfile } from "../lib/dashboardMessaging";
import { hydrateProfileStrength } from "../lib/profileStrength";
import { mergeProfilesForDisplay, normalizeProfileForDisplay } from "../lib/profileNormalization";
import { useNavigate, useSearchParams } from "react-router-dom";
import VoiceIntake from "../components/onboarding/VoiceIntake";
import { getVoiceIntakeCenterView } from "../lib/voiceIntakeRouting";

export { getVoiceIntakeCenterView };

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

function buildFallbackProfile(isOpenToMatches = true) {
  return {
    candidate_id: null,
    candidateId: null,
    avatar: null,
    isOpenToMatches,
    strength: "Building",
    strengthPercent: 0,
    name: "",
    email: "",
    phone: "",
    headline: "",
    location: "",
    bio: "",
    experience: [],
    education: [],
    keySkills: [],
    experience_years: null,
    availability: "",
    preferred_roles: [],
    certifications: [],
    additional_information: "",
    voice_intake_resume: null,
  };
}

function hydrateDisplayProfile(profile) {
  return hydrateProfileStrength(normalizeProfileForDisplay(profile));
}

function ResizeHandle({ testId, subtle = false }) {
  return (
    <PanelResizeHandle
      data-testid={testId}
      className="group relative w-[6px] shrink-0 bg-transparent"
    >
      <div
        className={`absolute inset-y-0 left-1/2 -translate-x-1/2 w-px transition-colors ${
          subtle
            ? "bg-transparent group-hover:bg-black/[0.06] group-data-[resize-handle-active]:bg-black/[0.14]"
            : "bg-black/[0.05] group-hover:bg-black/[0.15] group-data-[resize-handle-active]:bg-black/[0.28]"
        }`}
      />
    </PanelResizeHandle>
  );
}

function Dashboard() {
  const navigate = useNavigate();
  const stored = React.useMemo(() => loadOnboardingState(), []);
  const [candidateId, setCandidateId] = React.useState(() => loadOnboardingState().candidateId ?? null);
  const [candidateToken] = React.useState(() => loadOnboardingState().candidateToken ?? null);
  const isOpenToMatches = stored.isOpenToMatches ?? true;
  const [settingsOpen, setSettingsOpen] = React.useState(false);

  const [showWeakProfilePopup, setShowWeakProfilePopup] = React.useState(false);
  const popupShownThisSessionRef = React.useRef(false);

  const [activeTab, setActiveTab] = React.useState(() => {
    if (stored.newlyOnboarded) {
      saveOnboardingState({ ...stored, newlyOnboarded: false });
      return "profile";
    }
    return "new-jobs";
  });

  const [userProfile, setUserProfile] = React.useState(() =>
    hydrateDisplayProfile(buildFallbackProfile(isOpenToMatches))
  );
  // Locked on first non-empty profile load; never updated by resume replacement.
  const footerIdentityRef = React.useRef({ name: "", email: "" });
  const [footerIdentity, setFooterIdentity] = React.useState({ name: "", email: "" });
  const voiceIntakeResume = userProfile.voice_intake_resume;
  const voiceIntakeResumeQuestion = voiceIntakeResume?.current_question || voiceIntakeResume?.next_question || "";
  const voiceIntakeCurrentQuestion = voiceIntakeResume?.current_question || "";
  const voiceIntakeInProgress =
    voiceIntakeResume?.status === "in_progress" &&
    Boolean(voiceIntakeResume?.has_open_question) &&
    Boolean(voiceIntakeCurrentQuestion);
  const voiceIntakeCompleted = isVoiceIntakeCompleteStatus(voiceIntakeResume?.status);
  const voiceIntakeCenterView = getVoiceIntakeCenterView(voiceIntakeResume);

  // All state declarations up front so effects can reference them
  const [chats, setChats] = React.useState([
    { id: "msg-1", sender: "eve", content: "Hi there — I'm Eve, your career partner on Pontis. How can I help you today?" },
  ]);
  const [chatRestored, setChatRestored] = React.useState(false);
  const [inputValue, setInputValue] = React.useState("");
  const [availableJobs, setAvailableJobs] = React.useState([]);
  const [documents, setDocuments] = React.useState({ resume: null, certificates: [] });
  const [docsLoading, setDocsLoading] = React.useState(false);
  const [selectedJob, setSelectedJob] = React.useState(null);
  const [sending, setSending] = React.useState(false);
  const [jobsLoading, setJobsLoading] = React.useState(true);
  const [jobsError, setJobsError] = React.useState(false);
  const [centerView, setCenterView] = React.useState("swipe"); // "swipe" | "chat" | "voice"
  // Tracks whether the user has explicitly chosen a center view (popup, toggle, mic).
  // When true, the auto-routing effect must not override their choice.
  const userChoseCenterViewRef = React.useRef(false);
  // Snapshot of the profile used to start VoiceIntake — always fetched fresh before mounting.
  const [voiceIntakeProfile, setVoiceIntakeProfile] = React.useState(null);
  const [opportunitiesCount, setOpportunitiesCount] = React.useState(0);

  // Load real profile from PostgreSQL on mount
  React.useEffect(() => {
    if (!candidateId) return;
    console.log("Current candidateId:", candidateId);
    axios
      .get(`${API}/candidate/${candidateId}/profile`)
      .then((res) => {
        const data = res.data;
        const profileCandidateId = data.candidate_id ?? data.candidateId ?? candidateId ?? null;
        if (profileCandidateId && profileCandidateId !== candidateId) {
          setCandidateId(profileCandidateId);
          saveOnboardingState({ ...loadOnboardingState(), candidateId: profileCandidateId });
        }
        const backendProfile = {
          candidate_id: profileCandidateId,
          candidateId: profileCandidateId,
          avatar: data.photo_url ?? null,
          name: data.name ?? "",
          email: data.email ?? "",
          phone: data.phone ?? "",
          headline: data.headline ?? "",
          location: data.location ?? "",
          bio: data.bio ?? "",
          experience: data.experience ?? [],
          education: data.education ?? [],
          keySkills: data.keySkills ?? [],
          experience_years: data.experience_years ?? null,
          availability: data.availability ?? "",
          preferred_roles: data.preferred_roles ?? [],
          certifications: data.certifications ?? [],
          additional_information: data.additional_information ?? "",
          voice_intake_resume: data.voice_intake_resume ?? null,
          profile_strength_percent: data.profile_strength_percent ?? data.strengthPercent,
          profile_strength_label: data.profile_strength_label ?? data.strength,
        };
        const cachedProfile = stored.parsedProfile ?? buildFallbackProfile(isOpenToMatches);
        setUserProfile((prev) =>
          hydrateDisplayProfile(
            mergeProfilesForDisplay(
              { ...backendProfile, isOpenToMatches: prev.isOpenToMatches },
              {
                ...cachedProfile,
                isOpenToMatches: prev.isOpenToMatches,
                voice_intake_resume: data.voice_intake_resume ?? cachedProfile.voice_intake_resume ?? null,
              }
            )
          )
        );
        if (!footerIdentityRef.current.name && (backendProfile.name || backendProfile.email)) {
          const locked = { name: backendProfile.name || "", email: backendProfile.email || "" };
          footerIdentityRef.current = locked;
          setFooterIdentity(locked);
        }
      })
      .catch(() => {
        const parsed = stored.parsedProfile ?? {};
        setUserProfile((prev) => hydrateDisplayProfile(mergeProfilesForDisplay(buildFallbackProfile(prev.isOpenToMatches), {
          candidate_id: candidateId,
          candidateId,
          avatar: null,
          isOpenToMatches: prev.isOpenToMatches,
          name: parsed.name ?? "",
          email: parsed.email ?? "",
          phone: parsed.phone ?? "",
          headline: parsed.headline ?? "",
          location: parsed.location ?? "",
          bio: parsed.bio ?? "",
          experience: parsed.experience ?? [],
          education: parsed.education ?? [],
          keySkills: parsed.keySkills ?? [],
          experience_years: parsed.experience_years ?? null,
          availability: parsed.availability ?? "",
          preferred_roles: parsed.preferred_roles ?? [],
          certifications: parsed.certifications ?? [],
          additional_information: parsed.additional_information ?? "",
          voice_intake_resume: parsed.voice_intake_resume ?? null,
          profile_strength_percent: parsed.profile_strength_percent ?? parsed.strengthPercent,
          profile_strength_label: parsed.profile_strength_label ?? parsed.strength,
        })));
      });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidateId]); // isOpenToMatches intentionally excluded — toggle state is preserved via prev

  React.useEffect(() => {
    // Backend is the single source of truth for the initial view.
    // Skip if the user has already made an explicit navigation choice.
    if (voiceIntakeCenterView !== null && !userChoseCenterViewRef.current) {
      setCenterView(voiceIntakeCenterView);
    }
  }, [voiceIntakeCenterView]);

  const refreshProfile = React.useCallback(async () => {
    if (!candidateId) return null;
    try {
      const res = await axios.get(`${API}/candidate/${candidateId}/profile`);
      const data = res.data;
      const profileCandidateId = data.candidate_id ?? data.candidateId ?? candidateId ?? null;
      if (profileCandidateId && profileCandidateId !== candidateId) {
        setCandidateId(profileCandidateId);
        saveOnboardingState({ ...loadOnboardingState(), candidateId: profileCandidateId });
      }
      const hasPhotoUrl = Object.prototype.hasOwnProperty.call(data || {}, "photo_url");
      // Build freshProfile synchronously before setUserProfile so callers
      // (e.g. mic-click) always receive the real backend state, not undefined.
      let freshProfile;
      setUserProfile((prev) => {
        freshProfile = hydrateDisplayProfile(mergeProfilesForDisplay({
          ...data,
          candidate_id: profileCandidateId ?? prev.candidate_id ?? prev.candidateId ?? null,
          candidateId: profileCandidateId ?? prev.candidate_id ?? prev.candidateId ?? null,
          avatar: hasPhotoUrl ? (data.photo_url ?? null) : prev.avatar,
          isOpenToMatches: prev.isOpenToMatches,
        }, {
          ...prev,
          candidate_id: profileCandidateId ?? prev.candidate_id ?? prev.candidateId ?? null,
          candidateId: profileCandidateId ?? prev.candidate_id ?? prev.candidateId ?? null,
          isOpenToMatches: prev.isOpenToMatches,
          name: prev.name,
          email: prev.email,
          phone: prev.phone,
          headline: prev.headline,
          location: prev.location,
          bio: prev.bio,
          experience: prev.experience,
          education: prev.education,
          keySkills: prev.keySkills,
          experience_years: prev.experience_years,
          availability: prev.availability,
          preferred_roles: prev.preferred_roles,
          certifications: prev.certifications,
          additional_information: prev.additional_information,
          voice_intake_resume: data.voice_intake_resume ?? prev.voice_intake_resume ?? null,
          profile_strength_percent: prev.profile_strength_percent,
          profile_strength_label: prev.strength,
        }));
        return freshProfile;
      });
      // React batches the state update — freshProfile is assigned synchronously
      // inside the setter callback above, so it is always defined here.
      // For the mic-click resume path we also build it directly from backend data
      // to guarantee voice_intake_resume is never stale.
      const directFresh = hydrateDisplayProfile(mergeProfilesForDisplay(
        {
          ...data,
          candidate_id: profileCandidateId,
          candidateId: profileCandidateId,
          avatar: hasPhotoUrl ? (data.photo_url ?? null) : undefined,
          voice_intake_resume: data.voice_intake_resume ?? null,
        },
        { voice_intake_resume: data.voice_intake_resume ?? null }
      ));
      console.log(
        "[refreshProfile] voice_intake_resume from backend:",
        JSON.stringify(data.voice_intake_resume ?? null)
      );
      return directFresh;
    } catch (err) {
      console.error("refreshProfile failed", err);
      return null;
    }
  }, [candidateId]);

  const handlePhotoChange = React.useCallback((url) => {
    setUserProfile((prev) => ({ ...prev, avatar: url }));
    refreshProfile();
  }, [refreshProfile]);

  const handleLogout = React.useCallback(() => {
    clearOnboardingState();
    navigate("/");
  }, [navigate]);

  const handleDeleteSuccess = React.useCallback(() => {
    clearOnboardingState();
    navigate("/");
  }, [navigate]);

  const handleToggleOpenToMatches = React.useCallback(() => {
    setUserProfile((prev) => {
      const next = { ...prev, isOpenToMatches: !prev.isOpenToMatches };
      const s = loadOnboardingState();
      saveOnboardingState({ ...s, isOpenToMatches: next.isOpenToMatches });
      return next;
    });
  }, []);

  // Restore persisted chat history from backend on mount
  React.useEffect(() => {
    if (!candidateId) return;
    axios
      .get(`${API}/candidate/${candidateId}/chat`)
      .then((res) => {
        const msgs = res.data?.messages;
        if (!Array.isArray(msgs) || msgs.length === 0) return;
        const restored = msgs.map((m, i) => ({
          id: `r-${i}`,
          sender: m.role === "user" ? "user" : "eve",
          content: m.content,
        }));
        setChats(restored);
        setChatRestored(true);
      })
      .catch(() => {});
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidateId]);

  // Update greeting once real profile loads - personalise with name, never ask for already-known info
  React.useEffect(() => {
    if (chatRestored) return;
    const firstName = userProfile.name?.split(" ")[0];
    const hasResume = documents?.resume != null;
    const profileComplete = userProfile.headline && userProfile.keySkills?.length > 0;
    const greeting = buildDashboardEveGreetingFromProfile({
      firstName,
      profileComplete,
      hasResume,
      voiceIntakeResume,
    });
    if (!greeting) return;
    setChats((prev) =>
      prev[0]?.id === "msg-1"
        ? [{ ...prev[0], content: greeting }, ...prev.slice(1)]
        : prev
    );
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatRestored, voiceIntakeInProgress, voiceIntakeResumeQuestion, userProfile.name, userProfile.headline, userProfile.keySkills?.length, documents?.resume]);
  // Opportunities count
  React.useEffect(() => {
    if (!candidateId) return;
    const fetchCount = () => {
      const oppPromise = axios
        .get(`${API}/candidate/${candidateId}/opportunities`)
        .then((res) => (res.data || []).filter((o) => !o.candidate_response).length)
        .catch(() => 0);
      const notifPromise = axios
        .get(`${API}/candidate/${candidateId}/notifications`)
        .then((res) => (res.data || []).filter((n) => !n.is_read).length)
        .catch(() => 0);
      Promise.all([oppPromise, notifPromise]).then(([oppCount, notifCount]) => {
        setOpportunitiesCount(oppCount + notifCount);
      });
    };
    fetchCount();
    const interval = setInterval(fetchCount, 30000);
    return () => clearInterval(interval);
  }, [candidateId]);

  // Show weak-profile popup once per session if strength < 75 on first load
  React.useEffect(() => {
    if (popupShownThisSessionRef.current) return;
    if (userProfile.strengthPercent > 0 && userProfile.strengthPercent < 75) {
      popupShownThisSessionRef.current = true;
      const t = setTimeout(() => setShowWeakProfilePopup(true), 800);
      return () => clearTimeout(t);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userProfile.strengthPercent]);

  // Load real job recommendations from backend
  const fetchJobs = React.useCallback(() => {
    if (!candidateId) return;
    setJobsError(false);
    axios
      .get(`${API}/candidate/${candidateId}/jobs`)
      .then((res) => {
        setAvailableJobs(res.data || []);
        setSelectedJob((prev) => prev ?? (res.data?.[0] || null));
        setJobsLoading(false);
      })
      .catch(() => {
        setJobsError(true);
        setJobsLoading(false);
      });
  }, [candidateId]);

  React.useEffect(() => {
    fetchJobs();
    const interval = setInterval(fetchJobs, 60000);
    return () => clearInterval(interval);
  }, [fetchJobs]);

  React.useEffect(() => {
    if (!candidateId) return;
    setDocsLoading(true);
    axios
      .get(`${API}/candidate/${candidateId}/documents`)
      .then((res) => setDocuments(res.data))
      .catch(() => {})
      .finally(() => setDocsLoading(false));
  }, [candidateId]);

  const sessionIdRef = React.useRef(
    `sess-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  );

  const _sendToEve = React.useCallback(async (historyPayload, currentChats) => {
    setSending(true);
    try {
      const res = await axios.post(`${API}/chat`, {
        messages: historyPayload,
        session_id: sessionIdRef.current,
        candidate_id: candidateId,
      });
      const reply = res?.data?.reply?.trim();
      if (reply) {
        setChats((prev) => [
          ...prev,
          { id: `e-${Date.now()}`, sender: "eve", content: reply },
        ]);
      } else {
        toast.error("Eve didn't respond. Try again?");
      }
      if (res?.data?.profile_updates && candidateId) {
        await refreshProfile();
      }
    } catch (err) {
      console.error("chat error", err);
      toast.error("Couldn't reach Eve right now.");
    } finally {
      setSending(false);
    }
  }, [candidateId, refreshProfile]);

  const handleSendMessage = async (e) => {
    e.preventDefault();
    const text = inputValue.trim();
    if (!text || sending) return;

    const userMsg = { id: `u-${Date.now()}`, sender: "user", content: text };
    const nextChats = [...chats, userMsg];
    setChats(nextChats);
    setInputValue("");

    const historyPayload = nextChats
      .filter((c) => c.sender === "user" || c.sender === "eve")
      .map((c) => ({
        role: c.sender === "user" ? "user" : "assistant",
        content: c.content,
      }));
    await _sendToEve(historyPayload, nextChats);
  };

  // Suggestion chips act as profile-improvement prompts: Eve asks the candidate
  // the question; the instruction is not shown as a user bubble.
  const handleSuggestionClick = React.useCallback(async (suggestion) => {
    if (sending) return;
    const instruction = `[PROFILE_QUESTION] Please ask me the following question to help complete my profile: "${suggestion}"`;
    const historyPayload = [
      ...chats
        .filter((c) => c.sender === "user" || c.sender === "eve")
        .map((c) => ({
          role: c.sender === "user" ? "user" : "assistant",
          content: c.content,
        })),
      { role: "user", content: instruction },
    ];
    await _sendToEve(historyPayload, chats);
  }, [sending, chats, _sendToEve]);

  const handleResumeReplaced = React.useCallback((filename, newProfile) => {
    setDocuments((prev) => ({ ...prev, resume: { filename } }));
    refreshProfile();
  }, [refreshProfile]);

  const handleCertUploaded = React.useCallback((cert) => {
    setDocuments((prev) => ({ ...prev, certificates: [...prev.certificates, cert] }));
  }, []);

  const handleCertReplaced = React.useCallback((certId, filename) => {
    setDocuments((prev) => ({
      ...prev,
      certificates: prev.certificates.map((c) =>
        c.id === certId ? { ...c, filename } : c
      ),
    }));
  }, []);

  const handleResumeDeleted = React.useCallback(() => {
    setDocuments((prev) => ({ ...prev, resume: null }));
  }, []);

  const handleCertDeleted = React.useCallback((certId) => {
    setDocuments((prev) => ({
      ...prev,
      certificates: prev.certificates.filter((c) => c.id !== certId),
    }));
  }, []);

  const handleTrackJob = async (jobId) => {
    const job = availableJobs.find((j) => j.id === jobId);
    if (!job || !candidateId) return;
    const willTrack = !job.tracked;
    setAvailableJobs((prev) =>
      prev.map((j) => (j.id === jobId ? { ...j, tracked: willTrack } : j))
    );
    try {
      if (willTrack) {
        await axios.post(`${API}/candidate/${candidateId}/jobs/${jobId}/track`);
        toast.success(`Tracking ${job.company}`);
      } else {
        await axios.delete(`${API}/candidate/${candidateId}/jobs/${jobId}/track`);
        toast("Untracked");
      }
    } catch {
      setAvailableJobs((prev) =>
        prev.map((j) => (j.id === jobId ? { ...j, tracked: !willTrack } : j))
      );
      toast.error("Could not update tracking.");
    }
  };

  const handleDismissJob = async (jobId, reason = null) => {
    const job = availableJobs.find((j) => j.id === jobId);
    if (!job || !candidateId) return;
    setAvailableJobs((prev) => prev.filter((j) => j.id !== jobId));
    if (selectedJob?.id === jobId) {
      setSelectedJob(availableJobs.find((j) => j.id !== jobId) || null);
    }
    toast("Removed from feed", { description: "You won't see this again." });
    try {
      await axios.post(`${API}/candidate/${candidateId}/jobs/${jobId}/dismiss`, reason ? { reason } : {});
    } catch {
      fetchJobs();
    }
  };

  return (
    <div
      className="h-screen w-full bg-[#FBFBF9] text-[#1F1F1F] overflow-hidden flex flex-col"
      data-testid="app-shell"
    >
      <Toaster position="top-right" richColors closeButton />

      {/* Dashboard top header with Bell */}
      <div className="shrink-0 flex items-center justify-end px-5 py-2 border-b border-black/[0.05]">
        <button
          data-testid="header-bell-btn"
          onClick={() => setActiveTab("opportunities")}
          className="relative p-1.5 rounded-lg text-[#4A4A48] hover:bg-black/[0.04] transition-colors"
          aria-label="Notifications"
        >
          <Bell className="w-[18px] h-[18px]" strokeWidth={1.5} />
          {opportunitiesCount > 0 && (
            <span
              data-testid="header-bell-badge"
              className="absolute -top-0.5 -right-0.5 min-w-[16px] h-[16px] px-1 rounded-full bg-[#1F1F1F] text-white text-[9px] font-medium flex items-center justify-center"
            >
              {opportunitiesCount}
            </span>
          )}
        </button>
      </div>

      {/* Weak profile popup */}
      {showWeakProfilePopup && (
        <div
          data-testid="weak-profile-popup"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/30"
          onClick={() => setShowWeakProfilePopup(false)}
        >
          <div
            className="bg-white rounded-2xl shadow-xl px-8 py-7 max-w-sm w-full mx-4 flex flex-col gap-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-3">
              <span className="w-2.5 h-2.5 rounded-full bg-[#E11D48] shrink-0" />
              <p className="text-[15px] font-medium text-[#1F1F1F]">
                Your profile isn't strong yet
              </p>
            </div>
            <p className="text-[13px] text-[#4A4A48] leading-relaxed">
              Your profile strength is {userProfile.strengthPercent}%. Complete a quick voice chat with Eve to unlock better job matches and reach a strong profile.
            </p>
            <div className="flex gap-2">
              <button
                data-testid="weak-profile-chat-btn"
                onClick={() => {
                  setShowWeakProfilePopup(false);
                  userChoseCenterViewRef.current = true;
                  setCenterView("chat");
                }}
                className="flex-1 bg-[#1F1F1F] text-white text-[13px] font-medium rounded-full py-2.5 hover:bg-black transition-colors"
              >
                Chat with Eve
              </button>
              <button
                data-testid="weak-profile-dismiss-btn"
                onClick={() => {
                  setShowWeakProfilePopup(false);
                  userChoseCenterViewRef.current = true;
                }}
                className="flex-1 bg-black/[0.05] text-[#1F1F1F] text-[13px] font-medium rounded-full py-2.5 hover:bg-black/[0.09] transition-colors"
              >
                Maybe later
              </button>
            </div>
          </div>
        </div>
      )}

      <PanelGroup direction="horizontal" className="flex-1 min-h-0">
        <Panel id="left-panel" order={1} defaultSize={18} minSize={12} maxSize={28} className="h-full">
          <Sidebar
            activeTab={activeTab}
            setActiveTab={setActiveTab}
            userProfile={userProfile}
            footerIdentity={footerIdentity.name || footerIdentity.email ? footerIdentity : undefined}
            jobsCount={availableJobs.filter((j) => !j.viewed).length}
            opportunitiesCount={opportunitiesCount}
            recentActivity={MOCK_RECENT_ACTIVITY}
            onLogout={handleLogout}
            onSettings={() => setSettingsOpen(true)}
          />
        </Panel>

        <ResizeHandle testId="resize-handle-left" />

        <Panel id="center-panel" order={2} defaultSize={32} minSize={22} className="h-full">
          <div className="h-full flex flex-col bg-[#FBFBF9] min-h-0">
            {/* Toggle bar */}
            <div className="shrink-0 flex items-center gap-1 px-4 pt-3 pb-2 border-b border-black/[0.05]">
              <button
                onClick={() => {
                  userChoseCenterViewRef.current = true;
                  setCenterView("swipe");
                }}
                className={`px-3 py-1.5 rounded-lg text-[12.5px] transition-colors ${
                  centerView === "swipe"
                    ? "bg-black/[0.06] text-[#1F1F1F] font-medium"
                    : "text-[#9A9A98] hover:text-[#4A4A48]"
                }`}
              >
                Jobs for you
              </button>
              <button
                onClick={() => {
                  userChoseCenterViewRef.current = true;
                  setCenterView("chat");
                }}
                className={`px-3 py-1.5 rounded-lg text-[12.5px] transition-colors ${
                  centerView === "chat" || centerView === "voice"
                    ? "bg-black/[0.06] text-[#1F1F1F] font-medium"
                    : "text-[#9A9A98] hover:text-[#4A4A48]"
                }`}
              >
                Chat with Eve
              </button>
            </div>

            {centerView === "voice" ? (
              <div className="flex-1 overflow-y-auto eve-scroll px-6 py-8">
              <VoiceIntake
                  firstName={(voiceIntakeProfile || userProfile).name?.split(" ")[0] || "there"}
              candidateId={candidateId}
              candidateProfile={voiceIntakeProfile || userProfile}
              onComplete={(result) => {
                if (result?.profile || result?.profile_updates) {
                  setUserProfile((prev) =>
                    hydrateDisplayProfile(
                      mergeProfilesForDisplay(prev, result.profile || result.profile_updates || {})
                    )
                  );
                }
                // Always route to chat immediately; the voiceIntakeCenterView effect
                // will override to "swipe" if the backend confirms completed.
                setCenterView("chat");
                refreshProfile().then(() => {
                  setUserProfile((latest) => {
                    const backendStatus = latest.voice_intake_resume?.status;
                    const completed = isVoiceIntakeCompleteStatus(backendStatus);
                    const s = loadOnboardingState();
                    saveOnboardingState({ ...s, voiceIntakeCompleted: completed });
                    return latest;
                  });
                });
              }}
            />
              </div>
            ) : centerView === "swipe" ? (
              jobsLoading ? (
                <div className="flex-1 flex items-center justify-center">
                  <div className="flex items-center gap-2 text-[#9A9A98]">
                    <span className="w-2 h-2 rounded-full bg-[#B5B5B3] animate-pulse" />
                    <span className="w-2 h-2 rounded-full bg-[#B5B5B3] animate-pulse" style={{ animationDelay: "120ms" }} />
                    <span className="w-2 h-2 rounded-full bg-[#B5B5B3] animate-pulse" style={{ animationDelay: "240ms" }} />
                  </div>
                </div>
              ) : jobsError ? (
                <div className="flex-1 flex flex-col items-center justify-center gap-3 px-8 text-center">
                  <p className="text-[14px] text-[#1F1F1F]">Couldn't load recommendations</p>
                  <button
                    onClick={fetchJobs}
                    className="text-[12.5px] text-[#4A4A48] underline underline-offset-2 hover:text-[#1F1F1F]"
                  >
                    Try again
                  </button>
                </div>
              ) : (
              <SwipeJobDeck
                  jobs={availableJobs}
                  candidateId={candidateId}
                  onJobsChange={fetchJobs}
                  onDismissJob={handleDismissJob}
                />
              )
            ) : (
              <ChatHub
                chats={chats}
                inputValue={inputValue}
                setInputValue={setInputValue}
                onSend={handleSendMessage}
                sending={sending}
                quickActions={getDynamicChatSuggestions(userProfile)}
                onSuggestionClick={handleSuggestionClick}
                onMicClick={async () => {
                  userChoseCenterViewRef.current = true;
                  const fresh = await refreshProfile();
                  setVoiceIntakeProfile(fresh);
                  setCenterView("voice");
                }}
              />
            )}
          </div>
        </Panel>

        <ResizeHandle testId="resize-handle-right" subtle />

        <Panel id="right-panel" order={3} defaultSize={50} minSize={30} className="h-full">
          <LivingProfile
            activeTab={activeTab}
            userProfile={userProfile}
            jobs={availableJobs}
            documents={documents}
            docsLoading={docsLoading}
            candidateId={candidateId}
            selectedJob={selectedJob}
            setSelectedJob={setSelectedJob}
            onTrackJob={handleTrackJob}
            onDismissJob={handleDismissJob}
            onToggleOpenToMatches={handleToggleOpenToMatches}
            onResumeReplaced={handleResumeReplaced}
            onCertUploaded={handleCertUploaded}
            onCertReplaced={handleCertReplaced}
            onResumeDeleted={handleResumeDeleted}
            onCertDeleted={handleCertDeleted}
            onInterested={fetchJobs}
            onPhotoChange={handlePhotoChange}
            onJobViewed={(jobId) =>
              setAvailableJobs((prev) =>
                prev.map((j) => (j.id === jobId ? { ...j, viewed: true } : j))
              )
            }
          />
        </Panel>
      </PanelGroup>
      <CandidateSettingsModal
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        candidateId={candidateId}
        candidateToken={candidateToken}
        candidateName={userProfile.name}
        candidateEmail={userProfile.email}
        onDeleteSuccess={handleDeleteSuccess}
      />
    </div>
  );
}

export default function DashboardGuard() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const candidateId = React.useMemo(() => {
    const fromUrl = searchParams.get("candidate_id");
    const candidateToken = searchParams.get("candidate_token");
    console.log("[LinkedIn] current URL:", window.location.href);
    console.log("[LinkedIn] candidate_id:", fromUrl);
    console.log("[LinkedIn] persisted state:", loadOnboardingState());
    if (fromUrl) {
      const s = loadOnboardingState();
      saveOnboardingState({ ...s, linkedInAuthenticated: true, candidateId: fromUrl, candidateToken: candidateToken || s.candidateToken || null });
      return fromUrl;
    }
    return loadOnboardingState().candidateId ?? null;
  }, [searchParams]);

  React.useEffect(() => {
    if (candidateId) {
      if (searchParams.get("candidate_id")) {
        console.log("[LinkedIn] redirecting to: /dashboard");
        navigate("/dashboard", { replace: true });
      }
      return;
    }
    navigate("/", { replace: true });
  }, [candidateId, navigate, searchParams]);

  if (!candidateId) return null;
  return <Dashboard />;
}
