// components/TokenSelector.tsx
'use client'

import { useState } from 'react';
import { searchTokens } from '@/app/actions/tokens';

export default function TokenSelector({ onSelect }) {
    const [results, setResults] = useState([]);

    const handleSearch = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const tokens = await searchTokens(e.target.value);
        setResults(tokens);
    };

    return (
        <div className="relative">
            <input
                type="text"
                onChange={handleSearch}
                placeholder="Search by name or symbol (e.g. BTC)"
                className="w-full p-2 border rounded"
            />
            {results.length > 0 && (
                <ul className="absolute z-10 w-full bg-white border mt-1 max-h-60 overflow-auto">
                    {results.map((token: any) => (
                        <li
                            key={token.address}
                            onClick={() => onSelect(token)}
                            className="p-2 hover:bg-gray-100 cursor-pointer flex justify-between"
                        >
                            <span>{token.name} ({token.symbol})</span>
                            <span className="text-xs text-gray-400">{token.address.slice(0, 6)}...</span>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
}

