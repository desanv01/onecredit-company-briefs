"""Render a verified selection as short Markdown."""

from .evidence import Fact


def render_summary(facts: list[Fact], selected_ids: list[str], follow_up_id: str | None = None) -> str:
    """Use the selected order, keeping the company fact as the introduction."""
    by_id = {fact.id: fact for fact in facts}
    company = by_id["company"]
    selected = [by_id[fact_id] for fact_id in selected_ids if fact_id != company.id]

    sections = [company.text]
    if selected:
        sections.append("\n".join(f"- {fact.text}" for fact in selected))

    if follow_up_id is not None and (follow_up_id == company.id or follow_up_id in selected_ids):
        follow_up = by_id[follow_up_id].follow_up
        if follow_up:
            sections.append(f"**Review next**\n\n{follow_up}")

    return "\n\n".join(sections)
