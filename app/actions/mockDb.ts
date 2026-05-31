import fs from 'fs';
import path from 'path';

const DB_FILE_PATH = path.join(process.cwd(), 'data', 'mock_user_db.json');

interface UserProfile {
    userId: string;
    credits: number;
    plan: 'basic' | 'pro' | 'enterprise';
}

function ensureDbExists() {
    const dir = path.dirname(DB_FILE_PATH);
    if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
    }
    if (!fs.existsSync(DB_FILE_PATH)) {
        const initialData: Record<string, UserProfile> = {
            'default-saas-user': {
                userId: 'default-saas-user',
                credits: 100, // 초기 크레딧: 100 토큰
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
            credits: 100,
            plan: 'basic'
        };
        writeDb(db);
    }
    return db[userId];
}

export async function getUserProfile(userId: string = 'default-saas-user'): Promise<UserProfile> {
    await initializeUserIfMissing(userId);
    const db = readDb();
    return db[userId];
}

export async function getUserCredits(userId: string = 'default-saas-user'): Promise<number> {
    const profile = await getUserProfile(userId);
    return profile.credits;
}

export async function deductUserCredits(userId: string = 'default-saas-user', amount: number): Promise<boolean> {
    await initializeUserIfMissing(userId);
    const db = readDb();
    const user = db[userId];
    if (user.credits < amount) {
        return false; // 크레딧 부족
    }
    user.credits -= amount;
    writeDb(db);
    return true;
}

export async function addUserCredits(userId: string = 'default-saas-user', amount: number): Promise<number> {
    await initializeUserIfMissing(userId);
    const db = readDb();
    const user = db[userId];
    user.credits += amount;
    writeDb(db);
    return user.credits;
}

export async function updateUserPlan(userId: string = 'default-saas-user', plan: 'basic' | 'pro' | 'enterprise'): Promise<UserProfile> {
    await initializeUserIfMissing(userId);
    const db = readDb();
    const user = db[userId];
    user.plan = plan;
    // 플랜 변경에 따른 기본 크레딧 보너스 지급
    if (plan === 'pro') {
        user.credits += 500;
    } else if (plan === 'enterprise') {
        user.credits += 2000;
    }
    writeDb(db);
    return user;
}
