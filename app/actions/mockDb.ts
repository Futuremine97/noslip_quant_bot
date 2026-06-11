import fs from 'fs';
import path from 'path';
import {
    InsufficientCreditBalanceError,
} from '@/server/creditLedger';
import {
    debitCredits,
    getCreditBalance,
    grantCredits,
} from '@/server/credits';

const DB_FILE_PATH = path.join(process.cwd(), 'data', 'mock_user_db.json');

interface UserProfile {
    userId: string;
    credits: number;
    plan: 'basic' | 'pro' | 'enterprise';
}

type BillingMutationOptions = {
    idempotencyKey?: string;
    reason?: string;
    metadata?: Record<string, unknown>;
};

function ensureDbExists() {
    const dir = path.dirname(DB_FILE_PATH);
    if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
    }
    if (!fs.existsSync(DB_FILE_PATH)) {
        const initialData: Record<string, UserProfile> = {
            'default-saas-user': {
                userId: 'default-saas-user',
                credits: 0,
                plan: 'basic'
            }
        };
        fs.writeFileSync(DB_FILE_PATH, JSON.stringify(initialData, null, 2), 'utf-8');
    }
}

function readDb(): Record<string, UserProfile> {
    ensureDbExists();
    try {
        const raw = fs.readFileSync(DB_FILE_PATH, 'utf-8');
        return JSON.parse(raw);
    } catch {
        return {};
    }
}

function writeDb(data: Record<string, UserProfile>) {
    ensureDbExists();
    fs.writeFileSync(DB_FILE_PATH, JSON.stringify(data, null, 2), 'utf-8');
}

export async function initializeUserIfMissing(userId: string): Promise<UserProfile> {
    const db = readDb();
    if (!db[userId]) {
        db[userId] = {
            userId,
            credits: 0,
            plan: 'basic'
        };
        writeDb(db);
    }
    return {
        ...db[userId],
        credits: await getCreditBalance(userId),
    };
}

export async function getUserProfile(userId: string = 'default-saas-user'): Promise<UserProfile> {
    await initializeUserIfMissing(userId);
    const db = readDb();
    return {
        ...db[userId],
        credits: await getCreditBalance(userId),
    };
}

export async function getUserCredits(userId: string = 'default-saas-user'): Promise<number> {
    const profile = await getUserProfile(userId);
    return profile.credits;
}

export async function deductUserCredits(userId: string = 'default-saas-user', amount: number): Promise<boolean> {
    try {
        await debitCredits({
            userId,
            amount,
            reason: 'legacy_feature_debit',
        });
        return true;
    } catch (error) {
        if (error instanceof InsufficientCreditBalanceError) {
            return false;
        }
        throw error;
    }
}

export async function addUserCredits(
    userId: string = 'default-saas-user',
    amount: number,
    options: BillingMutationOptions = {}
): Promise<number> {
    const result = await grantCredits({
        userId,
        amount,
        reason: options.reason || 'legacy_billing_credit',
        metadata: options.metadata,
        idempotencyKey: options.idempotencyKey,
    });
    return result.account.balance;
}

export async function updateUserPlan(
    userId: string = 'default-saas-user',
    plan: 'basic' | 'pro' | 'enterprise',
    options: BillingMutationOptions = {}
): Promise<UserProfile> {
    await initializeUserIfMissing(userId);
    const db = readDb();
    const user = db[userId];
    user.plan = plan;
    let bonusCredits = 0;
    if (plan === 'pro') {
        bonusCredits = 500;
    } else if (plan === 'enterprise') {
        bonusCredits = 2000;
    }
    writeDb(db);
    if (bonusCredits > 0) {
        await grantCredits({
            userId,
            amount: bonusCredits,
            reason: options.reason || `legacy_plan_bonus:${plan}`,
            metadata: options.metadata,
            idempotencyKey: options.idempotencyKey,
        });
    }
    return {
        ...user,
        credits: await getCreditBalance(userId),
    };
}
