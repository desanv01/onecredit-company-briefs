"""Extract traceable facts for a first-look supplier brief."""

from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True)
class Fact:
    id: str
    topic: str
    text: str
    source_paths: tuple[str, ...]
    required: bool = False
    follow_up: str | None = None


def _rm(value: int | float) -> str:
    amount = f"{abs(value):,.2f}".removesuffix(".00") if isinstance(value, float) else f"{abs(value):,}"
    return f"{'-' if value < 0 else ''}RM{amount}"


def _company_fact(record: dict) -> Fact:
    profile = record["profile"]
    follow_up = None
    if profile["status"].lower() == "winding up":
        follow_up = "Confirm the current winding-up status and who has authority to enter a new contract."
    return Fact(
        "company",
        "profile",
        f"{profile['companyName']} ({record['registrationNumber']}) is listed under "
        f"{profile['industry']} in {profile['state']}, with a recorded status of {profile['status'].lower()}. "
        f"Its stated business is “{profile['natureOfBusiness']}”.",
        (
            "/profile/companyName",
            "/registrationNumber",
            "/profile/industry",
            "/profile/state",
            "/profile/natureOfBusiness",
            "/profile/status",
        ),
        required=True,
        follow_up=follow_up,
    )


def _coverage_fact(years: list[dict], statements: list[tuple[int, dict]]) -> Fact | None:
    if not statements:
        return Fact(
            "coverage",
            "financials",
            "The record contains no financial statements.",
            ("/financials/years",),
            required=True,
            follow_up="Request the latest financial statements before reviewing financial capacity.",
        )

    reported = sorted({year["financialYear"] for _, year in statements})
    last_year = max(year["financialYear"] for year in years)
    missing = sorted(set(range(reported[0], last_year + 1)) - set(reported))
    sentences = []
    if len(reported) == 1:
        sentences.append(f"FY{reported[0]} is the only year with statements in the record.")
    if missing:
        periods = ", ".join(f"FY{year}" for year in missing)
        sentences.append(f"The record has no statements for {periods}.")
    if not sentences:
        return None
    return Fact(
        "coverage",
        "financials",
        " ".join(sentences),
        ("/financials/years",),
        required=True,
        follow_up="Request updated statements and fill any gaps before assessing a financial trend.",
    )


def _audit_fact(years: list[dict]) -> Fact | None:
    exceptions = []
    paths = []
    for index, year in sorted(enumerate(years), key=lambda item: item[1]["financialYear"], reverse=True):
        opinion = (year.get("audit") or {}).get("opinion")
        if opinion and opinion.lower() == "unqualified":
            continue
        period = f"FY{year['financialYear']}"
        if opinion:
            exceptions.append(f"The {period} audit opinion was “{opinion}”")
        else:
            exceptions.append(f"The record has no audit opinion for {period}")
        path = f"/financials/years/{index}"
        paths.append(f"{path}/financialYear")
        if opinion:
            paths.append(f"{path}/audit/opinion")
    if not exceptions:
        return None
    return Fact(
        "audit",
        "audit",
        ". ".join(exceptions) + ".",
        tuple(paths),
        required=True,
        follow_up="Read the relevant audit report and check how the underlying matters were addressed in later reporting.",
    )


def _profit(value: int) -> str:
    if value < 0:
        return f"an after-tax loss of {_rm(abs(value))}"
    return f"after-tax profit of {_rm(value)}"


def _snapshot_fact(statements: list[tuple[int, dict]]) -> Fact | None:
    index, year = statements[0]
    path = f"/financials/years/{index}"
    data = year["statements"]
    revenue = data.get("revenue")
    profit = data.get("profitAfterTax")
    paths = [f"{path}/financialYear", f"{path}/financialYearEnd"]
    metrics = []
    if revenue is not None:
        metrics.append(f"revenue of {_rm(revenue)}")
        paths.append(f"{path}/statements/revenue")
    if profit is not None:
        metrics.append(_profit(profit))
        paths.append(f"{path}/statements/profitAfterTax")
    if not metrics:
        return None

    text = f"Latest available statements (FY{year['financialYear']}, ended {year['financialYearEnd']}) show "
    text += " and ".join(metrics) + "."
    if len(statements) > 1:
        prior_index, prior = statements[1]
        prior_revenue = prior["statements"].get("revenue")
        prior_profit = prior["statements"].get("profitAfterTax")
        prior_path = f"/financials/years/{prior_index}"
        comparison = []
        if revenue is not None and prior_revenue is not None:
            comparison.append(f"revenue of {_rm(prior_revenue)}")
            paths.append(f"{prior_path}/statements/revenue")
        if profit is not None and prior_profit is not None:
            comparison.append(_profit(prior_profit))
            paths.append(f"{prior_path}/statements/profitAfterTax")
        if comparison:
            text += f" FY{prior['financialYear']} recorded " + " and ".join(comparison) + "."
            paths.append(f"{prior_path}/financialYear")
        comparable = (
            year["financialYear"] == prior["financialYear"] + 1
            and year["financialYearEnd"][5:] == prior["financialYearEnd"][5:]
        )
        if comparable:
            metric_path = "ratiosAndMetrics/profitability/operatingProfitMargin"
            margin = year["ratiosAndMetrics"]["profitability"].get("operatingProfitMargin")
            prior_margin = prior["ratiosAndMetrics"]["profitability"].get("operatingProfitMargin")
            if margin is not None and prior_margin is not None:
                change_points = round((margin - prior_margin) * 100, 1)
                if abs(change_points) >= 1:
                    direction = "rose" if change_points > 0 else "fell"
                    text += f" Operating margin {direction} {abs(change_points):g} percentage points to {margin:.1%}."
                    paths.extend((f"{path}/{metric_path}", f"{prior_path}/{metric_path}", f"{prior_path}/financialYearEnd"))
    audit = year.get("audit") or {}
    if (audit.get("opinion") or "").lower() == "unqualified":
        text += f" The FY{year['financialYear']} audit opinion was unqualified."
        paths.append(f"{path}/audit/opinion")
    follow_up = None
    if profit is not None and profit < 0:
        follow_up = "Ask for current management accounts to see whether the after-tax loss has continued."
    return Fact("snapshot", "financials", text, tuple(paths), follow_up=follow_up)


def _liquidity_fact(statements: list[tuple[int, dict]]) -> Fact | None:
    index, year = statements[0]
    path = f"/financials/years/{index}"
    data = year["statements"]
    paths = [f"{path}/financialYear"]
    metrics = []
    for key, label in (("operatingCashFlow", "operating cash flow"), ("borrowings", "borrowings")):
        value = data.get(key)
        if value is not None:
            metrics.append(f"{label} of {_rm(value)}")
            paths.append(f"{path}/statements/{key}")
    ratios = (year.get("ratiosAndMetrics") or {}).get("liquidityAndCashFlow") or {}
    quick_ratio = ratios.get("quickRatio")
    if quick_ratio is not None:
        metrics.append(f"a quick ratio of {quick_ratio:.2f}x")
        paths.append(f"{path}/ratiosAndMetrics/liquidityAndCashFlow/quickRatio")
    equity = data.get("totalEquity")
    if equity is not None and equity < 0:
        metrics.append(f"negative equity of {_rm(equity)}")
        paths.append(f"{path}/statements/totalEquity")
    if not metrics:
        return None

    follow_up = None
    cash_flow = data.get("operatingCashFlow")
    if equity is not None and equity < 0:
        follow_up = "Request an updated balance sheet and check how the company is funding operations."
    elif (cash_flow is not None and cash_flow < 0) or (quick_ratio is not None and quick_ratio < 1):
        follow_up = "Request a current cash-flow forecast and debt repayment schedule."
    text = f"FY{year['financialYear']} recorded {_join(metrics)}."
    return Fact("liquidity", "financials", text, tuple(paths), follow_up=follow_up)


def _join(items: list[str]) -> str:
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + ", and " + items[-1]


def _ownership_fact(relationships: dict) -> Fact | None:
    shareholders = relationships["shareholders"]
    if not shareholders:
        return None

    largest_share = max(shareholder["percentage"] for shareholder in shareholders)
    leaders = [shareholder for shareholder in shareholders if shareholder["percentage"] == largest_share]
    directors = {director["name"] for director in relationships["directors"]}
    names = []
    for shareholder in leaders:
        name = shareholder["name"]
        if shareholder["type"] == "individual" and name in directors:
            name += " (also a director)"
        names.append(name)
    listed = _join(names)
    if len(names) == 1:
        text = f"The largest recorded shareholder, {listed}, holds {largest_share:g}%."
    else:
        text = f"The largest recorded shareholders, {listed}, each hold {largest_share:g}%."
    return Fact("ownership", "relationships", text, ("/relationships/shareholders", "/relationships/directors"))


def _case_statuses(cases: list[dict]) -> str:
    counts = Counter(case["status"].lower() for case in cases)
    return ", ".join(
        f"{count} with judgment entered" if status == "judgment entered" else f"{count} {status}"
        for status, count in sorted(counts.items())
    )


def _case_fact(cases: list[dict]) -> Fact:
    path = "/litigation/cases"
    if not cases:
        return Fact("litigation", "litigation", "The record lists no court cases.", (path,))

    roles = []
    petitions = []
    exemplars = []
    for role in ("defendant", "plaintiff"):
        role_cases = [case for case in cases if case["role"] == role]
        if not role_cases:
            continue
        noun = "case" if len(role_cases) == 1 else "cases"
        roles.append(f"{role} in {len(role_cases)} {noun} ({_case_statuses(role_cases)})")
        role_petitions = [case for case in role_cases if case["caseType"].lower() == "winding-up petition"]
        if role_petitions:
            petitions.append(f"{role} ({_case_statuses(role_petitions)})")
        for status in ("pending", "judgment entered"):
            relevant = [case for case in role_cases if case["status"].lower() == status]
            if relevant:
                exemplars.append(
                    max(
                        relevant,
                        key=lambda case: (
                            case["caseType"].lower() == "winding-up petition",
                            case["claimAmount"] if case["claimAmount"] is not None else -1,
                        ),
                    )
                )

    text = "The company is listed as " + " and as ".join(roles) + "."
    if petitions:
        text += " Winding-up petitions list the company as " + " and as ".join(petitions) + "."
    material = bool(exemplars or petitions)
    for case in cases if len(cases) == 1 else exemplars[:2]:
        amount = "" if case["claimAmount"] is None else f", claim {_rm(case['claimAmount'])}"
        text += f" {case['caseNumber']} ({case['caseType']}, {case['role']}, {case['status'].lower()}{amount})."

    follow_up = None
    if petitions:
        follow_up = "Check the latest filings and orders on the winding-up petitions."
    elif material:
        follow_up = "Obtain the latest court filings and confirm current case status and any outstanding orders."
    return Fact("litigation", "litigation", text, (path,), required=material, follow_up=follow_up)


def build_facts(record: dict) -> list[Fact]:
    """Extract facts in fallback priority order with paths into the original record."""
    years = record["financials"]["years"]
    statements = sorted(
        (
            (index, year)
            for index, year in enumerate(years)
            if any(value is not None for value in (year.get("statements") or {}).values())
        ),
        key=lambda item: (item[1]["financialYear"], item[1]["financialYearEnd"]),
        reverse=True,
    )
    company = _company_fact(record)
    litigation = _case_fact(record["litigation"]["cases"])
    audit = _audit_fact(years)
    coverage = _coverage_fact(years, statements)
    snapshot = _snapshot_fact(statements) if statements else None
    liquidity = _liquidity_fact(statements) if statements else None
    ownership = _ownership_fact(record["relationships"])

    ordered = [
        company,
        litigation if litigation.required else None,
        audit,
        coverage,
        snapshot,
        liquidity,
        ownership,
        litigation if not litigation.required else None,
    ]
    return [fact for fact in ordered if fact is not None]
