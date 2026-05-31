'use server'

export interface EquitySearchResult {
    id: string;
    symbol: string;
    name: string;
    icon: string;
    sector?: string;
}

const SP500_FOCUSED_WATCHLIST: EquitySearchResult[] = [
    { id: 'AAPL', symbol: 'AAPL', name: 'Apple', icon: '', sector: 'Information Technology' },
    { id: 'MSFT', symbol: 'MSFT', name: 'Microsoft', icon: '', sector: 'Information Technology' },
    { id: 'NVDA', symbol: 'NVDA', name: 'NVIDIA', icon: '', sector: 'Information Technology' },
    { id: 'AMZN', symbol: 'AMZN', name: 'Amazon', icon: '', sector: 'Consumer Discretionary' },
    { id: 'META', symbol: 'META', name: 'Meta Platforms', icon: '', sector: 'Communication Services' },
    { id: 'TSLA', symbol: 'TSLA', name: 'Tesla', icon: '', sector: 'Consumer Discretionary' },
    { id: 'GOOGL', symbol: 'GOOGL', name: 'Alphabet Class A', icon: '', sector: 'Communication Services' },
    { id: 'GOOG', symbol: 'GOOG', name: 'Alphabet Class C', icon: '', sector: 'Communication Services' },
    { id: 'BRK.B', symbol: 'BRK.B', name: 'Berkshire Hathaway Class B', icon: '', sector: 'Financials' },
    { id: 'LLY', symbol: 'LLY', name: 'Eli Lilly', icon: '', sector: 'Health Care' },
    { id: 'JPM', symbol: 'JPM', name: 'JPMorgan Chase', icon: '', sector: 'Financials' },
    { id: 'V', symbol: 'V', name: 'Visa', icon: '', sector: 'Financials' },
    { id: 'MA', symbol: 'MA', name: 'Mastercard', icon: '', sector: 'Financials' },
    { id: 'XOM', symbol: 'XOM', name: 'Exxon Mobil', icon: '', sector: 'Energy' },
    { id: 'AVGO', symbol: 'AVGO', name: 'Broadcom', icon: '', sector: 'Information Technology' },
    { id: 'COST', symbol: 'COST', name: 'Costco', icon: '', sector: 'Consumer Staples' },
    { id: 'WMT', symbol: 'WMT', name: 'Walmart', icon: '', sector: 'Consumer Staples' },
    { id: 'PG', symbol: 'PG', name: 'Procter & Gamble', icon: '', sector: 'Consumer Staples' },
    { id: 'JNJ', symbol: 'JNJ', name: 'Johnson & Johnson', icon: '', sector: 'Health Care' },
    { id: 'HD', symbol: 'HD', name: 'Home Depot', icon: '', sector: 'Consumer Discretionary' },
    { id: 'ABBV', symbol: 'ABBV', name: 'AbbVie', icon: '', sector: 'Health Care' },
    { id: 'BAC', symbol: 'BAC', name: 'Bank of America', icon: '', sector: 'Financials' },
    { id: 'CRM', symbol: 'CRM', name: 'Salesforce', icon: '', sector: 'Information Technology' },
    { id: 'KO', symbol: 'KO', name: 'Coca-Cola', icon: '', sector: 'Consumer Staples' },
    { id: 'PEP', symbol: 'PEP', name: 'PepsiCo', icon: '', sector: 'Consumer Staples' },
    { id: 'MRK', symbol: 'MRK', name: 'Merck', icon: '', sector: 'Health Care' },
    { id: 'NFLX', symbol: 'NFLX', name: 'Netflix', icon: '', sector: 'Communication Services' },
    { id: 'ADBE', symbol: 'ADBE', name: 'Adobe', icon: '', sector: 'Information Technology' },
    { id: 'AMD', symbol: 'AMD', name: 'AMD', icon: '', sector: 'Information Technology' },
    { id: 'CSCO', symbol: 'CSCO', name: 'Cisco', icon: '', sector: 'Information Technology' },
    { id: 'ORCL', symbol: 'ORCL', name: 'Oracle', icon: '', sector: 'Information Technology' },
    { id: 'INTC', symbol: 'INTC', name: 'Intel', icon: '', sector: 'Information Technology' },
    { id: 'QCOM', symbol: 'QCOM', name: 'Qualcomm', icon: '', sector: 'Information Technology' },
    { id: 'TMO', symbol: 'TMO', name: 'Thermo Fisher Scientific', icon: '', sector: 'Health Care' },
    { id: 'UNH', symbol: 'UNH', name: 'UnitedHealth Group', icon: '', sector: 'Health Care' },
    { id: 'PFE', symbol: 'PFE', name: 'Pfizer', icon: '', sector: 'Health Care' },
    { id: 'MCD', symbol: 'MCD', name: 'McDonald’s', icon: '', sector: 'Consumer Discretionary' },
    { id: 'NKE', symbol: 'NKE', name: 'Nike', icon: '', sector: 'Consumer Discretionary' },
    { id: 'DIS', symbol: 'DIS', name: 'Disney', icon: '', sector: 'Communication Services' },
    { id: 'GE', symbol: 'GE', name: 'GE Aerospace', icon: '', sector: 'Industrials' },
    { id: 'CAT', symbol: 'CAT', name: 'Caterpillar', icon: '', sector: 'Industrials' },
    { id: 'HON', symbol: 'HON', name: 'Honeywell', icon: '', sector: 'Industrials' },
    { id: 'RTX', symbol: 'RTX', name: 'RTX', icon: '', sector: 'Industrials' },
    { id: 'SPY', symbol: 'SPY', name: 'SPDR S&P 500 ETF', icon: '', sector: 'Index ETF' },
    { id: 'IVV', symbol: 'IVV', name: 'iShares Core S&P 500 ETF', icon: '', sector: 'Index ETF' },
    { id: 'VOO', symbol: 'VOO', name: 'Vanguard S&P 500 ETF', icon: '', sector: 'Index ETF' },
];

function buildEquityIcon(symbol: string) {
    const initials = symbol.slice(0, 4).toUpperCase();
    return `data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='64' height='64' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='18' fill='%230f1b34'/%3E%3Ctext x='50%25' y='52%25' dominant-baseline='middle' text-anchor='middle' font-family='SF Pro Display, SF Pro Text, Helvetica Neue, Arial, sans-serif' font-size='20' font-weight='700' fill='%238af0c2'%3E${encodeURIComponent(initials)}%3C/text%3E%3C/svg%3E`;
}

const WATCHLIST = SP500_FOCUSED_WATCHLIST.map((item) => ({
    ...item,
    icon: buildEquityIcon(item.symbol),
}));

function normalizeTicker(value: string) {
    return value.trim().toUpperCase();
}

export async function searchSp500Equities(query: string): Promise<EquitySearchResult[]> {
    const normalized = normalizeTicker(query);
    if (!normalized) {
        return [];
    }

    const matches = WATCHLIST.filter((item) => {
        const haystack = `${item.symbol} ${item.name} ${item.sector || ''}`.toUpperCase();
        return haystack.includes(normalized);
    }).slice(0, 8);

    if (matches.some((item) => item.symbol === normalized)) {
        return matches;
    }

    return [
        {
            id: normalized,
            symbol: normalized,
            name: `Manual ticker ${normalized}`,
            sector: 'Manual lookup',
            icon: buildEquityIcon(normalized),
        },
        ...matches,
    ].slice(0, 8);
}
