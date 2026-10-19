"""Personal-mode sidebar: what a single local user still needs once the
account / billing sections are gone — cost visibility and backups."""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from services.usage import spend_by_model, spend_usd

_DAY = 86400


def render_personal_sidebar(user_id: int) -> None:
    st.sidebar.markdown("👤 **Personal mode** · local, single user")

    with st.sidebar.expander("💰 LLM spend"):
        week, month = spend_usd(user_id, 7 * _DAY), spend_usd(user_id, 30 * _DAY)
        c1, c2 = st.columns(2)
        c1.metric("Last 7 days", f"${week:.2f}")
        c2.metric("Last 30 days", f"${month:.2f}")
        rows = spend_by_model(user_id, 30 * _DAY)
        if rows:
            st.dataframe(
                [{"model": r["model"], "calls": r["calls"], "USD": round(r["usd"], 3)} for r in rows],
                hide_index=True,
            )
        st.caption(
            "Estimated from token usage (plus $0.01 per web search). "
            "Set LLM_BUDGET_USD in .env to cap spend over 30 days."
        )

    with st.sidebar.expander("💾 Backup & export"):
        from db.session import describe_database

        st.caption(
            f"Everything lives in one database ({describe_database()}). Copy it "
            "to back up, or export a portable JSON of all your data."
        )
        if st.button("Prepare full export (JSON)", key="personal_export_prepare"):
            from services.account_export import export_json

            st.session_state["personal_export"] = export_json(user_id)
        if st.session_state.get("personal_export"):
            st.download_button(
                "⬇️ Download export",
                data=st.session_state["personal_export"],
                file_name=f"joboffer_backup_{datetime.utcnow():%Y-%m-%d}.json",
                mime="application/json",
                key="personal_export_download",
            )
