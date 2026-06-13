//! NoSlip 생태계 Anchor 프로그램.
//!
//! NSQ(SPL 토큰)를 중심으로 4가지 유틸리티를 제공한다:
//!   1) AI 사용량 결제 (pay_for_usage)
//!   2) 스테이킹/우선순위 (stake / unstake)
//!   3) 봇/연합 평판 (submit_reputation)
//!   4) 연합 온체인 레지스트리 (register_federation / set_federation_status)
//!
//! ⚠️ 유틸리티 전용. 수익/배당/원금보장을 제공하지 않는다. 배포 전 법률 검토·감사 필수.

use anchor_lang::prelude::*;
use anchor_spl::token::{self, Burn, Mint, Token, TokenAccount, Transfer};

declare_id!("Nosxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx");

const CONFIG_SEED: &[u8] = b"config";
const STAKE_SEED: &[u8] = b"stake";
const REP_SEED: &[u8] = b"rep";
const FED_SEED: &[u8] = b"fed";

#[program]
pub mod noslip_ecosystem {
    use super::*;

    /// 최초 1회 설정.
    pub fn initialize(
        ctx: Context<Initialize>,
        usage_fee_per_unit: u64,
        min_stake_for_priority: u64,
        burn_on_usage: bool,
    ) -> Result<()> {
        let cfg = &mut ctx.accounts.config;
        cfg.admin = ctx.accounts.admin.key();
        cfg.nsq_mint = ctx.accounts.nsq_mint.key();
        cfg.treasury = ctx.accounts.treasury.key();
        cfg.usage_fee_per_unit = usage_fee_per_unit;
        cfg.min_stake_for_priority = min_stake_for_priority;
        cfg.burn_on_usage = burn_on_usage;
        cfg.bump = ctx.bumps.config;
        Ok(())
    }

    /// 관리자 파라미터 갱신.
    pub fn update_config(
        ctx: Context<AdminOnly>,
        usage_fee_per_unit: Option<u64>,
        min_stake_for_priority: Option<u64>,
        burn_on_usage: Option<bool>,
    ) -> Result<()> {
        let cfg = &mut ctx.accounts.config;
        if let Some(v) = usage_fee_per_unit {
            cfg.usage_fee_per_unit = v;
        }
        if let Some(v) = min_stake_for_priority {
            cfg.min_stake_for_priority = v;
        }
        if let Some(v) = burn_on_usage {
            cfg.burn_on_usage = v;
        }
        Ok(())
    }

    /// ── 유틸리티 1: AI 사용량 결제 ──
    /// units * usage_fee_per_unit 만큼 NSQ를 트레저리로 전송하거나 소각.
    pub fn pay_for_usage(ctx: Context<PayForUsage>, units: u64, memo: [u8; 32]) -> Result<()> {
        let cfg = &ctx.accounts.config;
        let amount = (units as u128)
            .checked_mul(cfg.usage_fee_per_unit as u128)
            .ok_or(EcoError::MathOverflow)?;
        require!(amount <= u64::MAX as u128, EcoError::MathOverflow);
        let amount = amount as u64;
        require!(amount > 0, EcoError::ZeroAmount);

        if cfg.burn_on_usage {
            token::burn(
                CpiContext::new(
                    ctx.accounts.token_program.to_account_info(),
                    Burn {
                        mint: ctx.accounts.nsq_mint.to_account_info(),
                        from: ctx.accounts.payer_ata.to_account_info(),
                        authority: ctx.accounts.payer.to_account_info(),
                    },
                ),
                amount,
            )?;
        } else {
            token::transfer(
                CpiContext::new(
                    ctx.accounts.token_program.to_account_info(),
                    Transfer {
                        from: ctx.accounts.payer_ata.to_account_info(),
                        to: ctx.accounts.treasury.to_account_info(),
                        authority: ctx.accounts.payer.to_account_info(),
                    },
                ),
                amount,
            )?;
        }

        emit!(UsagePaid {
            payer: ctx.accounts.payer.key(),
            units,
            amount,
            burned: cfg.burn_on_usage,
            memo,
        });
        Ok(())
    }

    /// ── 유틸리티 2: 스테이킹 (NSQ를 Vault로 예치) ──
    pub fn stake(ctx: Context<Stake>, amount: u64) -> Result<()> {
        require!(amount > 0, EcoError::ZeroAmount);
        token::transfer(
            CpiContext::new(
                ctx.accounts.token_program.to_account_info(),
                Transfer {
                    from: ctx.accounts.user_ata.to_account_info(),
                    to: ctx.accounts.vault.to_account_info(),
                    authority: ctx.accounts.user.to_account_info(),
                },
            ),
            amount,
        )?;

        let s = &mut ctx.accounts.stake_account;
        s.user = ctx.accounts.user.key();
        s.amount = s.amount.checked_add(amount).ok_or(EcoError::MathOverflow)?;
        s.priority_weight = priority_weight(s.amount);
        s.last_update = Clock::get()?.unix_timestamp;
        s.bump = ctx.bumps.stake_account;
        Ok(())
    }

    /// ── 스테이킹 해제 (Vault에서 사용자에게 반환) ──
    pub fn unstake(ctx: Context<Unstake>, amount: u64) -> Result<()> {
        let s = &mut ctx.accounts.stake_account;
        require!(amount > 0, EcoError::ZeroAmount);
        require!(amount <= s.amount, EcoError::InsufficientStake);

        let cfg = &ctx.accounts.config;
        let seeds: &[&[u8]] = &[CONFIG_SEED, &[cfg.bump]];
        let signer = &[seeds];
        token::transfer(
            CpiContext::new_with_signer(
                ctx.accounts.token_program.to_account_info(),
                Transfer {
                    from: ctx.accounts.vault.to_account_info(),
                    to: ctx.accounts.user_ata.to_account_info(),
                    authority: ctx.accounts.config.to_account_info(),
                },
                signer,
            ),
            amount,
        )?;

        s.amount = s.amount.checked_sub(amount).ok_or(EcoError::MathOverflow)?;
        s.priority_weight = priority_weight(s.amount);
        s.last_update = Clock::get()?.unix_timestamp;
        Ok(())
    }

    /// ── 유틸리티 3: 봇/연합 평판 (스테이킹 가중 투표) ──
    pub fn submit_reputation(
        ctx: Context<SubmitReputation>,
        bot_id: [u8; 32],
        delta: i64,
    ) -> Result<()> {
        let weight = ctx.accounts.stake_account.priority_weight.max(1) as i64;
        let signed = delta.signum() * weight;

        let rep = &mut ctx.accounts.reputation;
        if rep.bot_id == [0u8; 32] {
            rep.bot_id = bot_id;
            rep.bump = ctx.bumps.reputation;
        }
        require!(rep.bot_id == bot_id, EcoError::SeedMismatch);
        rep.score = rep.score.checked_add(signed).ok_or(EcoError::MathOverflow)?;
        rep.votes = rep.votes.checked_add(1).ok_or(EcoError::MathOverflow)?;
        rep.last_update = Clock::get()?.unix_timestamp;
        Ok(())
    }

    /// ── 유틸리티 4: 연합 온체인 레지스트리 ──
    pub fn register_federation(
        ctx: Context<RegisterFederation>,
        proposal_id: [u8; 32],
        members_hash: [u8; 32],
        mode: u8,
        squad_id: [u8; 32],
    ) -> Result<()> {
        let f = &mut ctx.accounts.federation;
        f.proposal_id = proposal_id;
        f.members_hash = members_hash;
        f.mode = mode;
        f.squad_id = squad_id;
        f.status = FederationStatus::Approved as u8;
        f.registrar = ctx.accounts.admin.key();
        f.created_at = Clock::get()?.unix_timestamp;
        f.bump = ctx.bumps.federation;
        emit!(FederationRegistered { proposal_id, mode });
        Ok(())
    }

    pub fn set_federation_status(
        ctx: Context<SetFederationStatus>,
        _proposal_id: [u8; 32],
        status: u8,
    ) -> Result<()> {
        require!(status <= FederationStatus::Executed as u8, EcoError::BadStatus);
        ctx.accounts.federation.status = status;
        Ok(())
    }
}

/// 우선순위 가중치: 정수 sqrt로 고래 편중 완화.
fn priority_weight(amount: u64) -> u64 {
    integer_sqrt(amount)
}

fn integer_sqrt(n: u64) -> u64 {
    if n == 0 {
        return 0;
    }
    let mut x = n;
    let mut y = (x + 1) / 2;
    while y < x {
        x = y;
        y = (x + n / x) / 2;
    }
    x
}

// ───────────────────────────── 계정 ─────────────────────────────
#[account]
pub struct Config {
    pub admin: Pubkey,
    pub nsq_mint: Pubkey,
    pub treasury: Pubkey,
    pub usage_fee_per_unit: u64,
    pub min_stake_for_priority: u64,
    pub burn_on_usage: bool,
    pub bump: u8,
}
impl Config {
    pub const LEN: usize = 8 + 32 * 3 + 8 * 2 + 1 + 1;
}

#[account]
pub struct StakeAccount {
    pub user: Pubkey,
    pub amount: u64,
    pub priority_weight: u64,
    pub last_update: i64,
    pub bump: u8,
}
impl StakeAccount {
    pub const LEN: usize = 8 + 32 + 8 + 8 + 8 + 1;
}

#[account]
pub struct Reputation {
    pub bot_id: [u8; 32],
    pub score: i64,
    pub votes: u64,
    pub last_update: i64,
    pub bump: u8,
}
impl Reputation {
    pub const LEN: usize = 8 + 32 + 8 + 8 + 8 + 1;
}

#[account]
pub struct Federation {
    pub proposal_id: [u8; 32],
    pub members_hash: [u8; 32],
    pub squad_id: [u8; 32],
    pub mode: u8,
    pub status: u8,
    pub registrar: Pubkey,
    pub created_at: i64,
    pub bump: u8,
}
impl Federation {
    pub const LEN: usize = 8 + 32 * 3 + 1 + 1 + 32 + 8 + 1;
}

#[repr(u8)]
pub enum FederationStatus {
    Proposed = 0,
    Approved = 1,
    Executed = 2,
}

// ───────────────────────────── Contexts ─────────────────────────────
#[derive(Accounts)]
pub struct Initialize<'info> {
    #[account(
        init,
        payer = admin,
        space = Config::LEN,
        seeds = [CONFIG_SEED],
        bump
    )]
    pub config: Account<'info, Config>,
    pub nsq_mint: Account<'info, Mint>,
    /// 트레저리 토큰 계정(NSQ ATA, owner = config PDA 권장)
    #[account(token::mint = nsq_mint)]
    pub treasury: Account<'info, TokenAccount>,
    #[account(mut)]
    pub admin: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct AdminOnly<'info> {
    #[account(mut, seeds = [CONFIG_SEED], bump = config.bump, has_one = admin)]
    pub config: Account<'info, Config>,
    pub admin: Signer<'info>,
}

#[derive(Accounts)]
pub struct PayForUsage<'info> {
    #[account(seeds = [CONFIG_SEED], bump = config.bump)]
    pub config: Account<'info, Config>,
    #[account(mut, address = config.nsq_mint)]
    pub nsq_mint: Account<'info, Mint>,
    #[account(mut, token::mint = nsq_mint)]
    pub payer_ata: Account<'info, TokenAccount>,
    /// 트레저리(소각이 아닐 때 수취). burn 시에도 계정은 전달되지만 미사용.
    #[account(mut, address = config.treasury)]
    pub treasury: Account<'info, TokenAccount>,
    pub payer: Signer<'info>,
    pub token_program: Program<'info, Token>,
}

#[derive(Accounts)]
pub struct Stake<'info> {
    #[account(seeds = [CONFIG_SEED], bump = config.bump)]
    pub config: Account<'info, Config>,
    #[account(
        init_if_needed,
        payer = user,
        space = StakeAccount::LEN,
        seeds = [STAKE_SEED, user.key().as_ref()],
        bump
    )]
    pub stake_account: Account<'info, StakeAccount>,
    #[account(mut, token::mint = config.nsq_mint)]
    pub user_ata: Account<'info, TokenAccount>,
    /// 스테이킹 Vault(NSQ ATA, authority = config PDA)
    #[account(mut, token::mint = config.nsq_mint)]
    pub vault: Account<'info, TokenAccount>,
    #[account(mut)]
    pub user: Signer<'info>,
    pub token_program: Program<'info, Token>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct Unstake<'info> {
    #[account(seeds = [CONFIG_SEED], bump = config.bump)]
    pub config: Account<'info, Config>,
    #[account(
        mut,
        seeds = [STAKE_SEED, user.key().as_ref()],
        bump = stake_account.bump,
        has_one = user
    )]
    pub stake_account: Account<'info, StakeAccount>,
    #[account(mut, token::mint = config.nsq_mint)]
    pub user_ata: Account<'info, TokenAccount>,
    #[account(mut, token::mint = config.nsq_mint)]
    pub vault: Account<'info, TokenAccount>,
    pub user: Signer<'info>,
    pub token_program: Program<'info, Token>,
}

#[derive(Accounts)]
#[instruction(bot_id: [u8; 32])]
pub struct SubmitReputation<'info> {
    #[account(seeds = [CONFIG_SEED], bump = config.bump)]
    pub config: Account<'info, Config>,
    /// 투표자 = 자신의 스테이크 PDA 소유자(seeds로 바인딩). 스테이크 가중치로 투표.
    #[account(
        seeds = [STAKE_SEED, voter.key().as_ref()],
        bump = stake_account.bump,
    )]
    pub stake_account: Account<'info, StakeAccount>,
    #[account(
        init_if_needed,
        payer = voter,
        space = Reputation::LEN,
        seeds = [REP_SEED, bot_id.as_ref()],
        bump
    )]
    pub reputation: Account<'info, Reputation>,
    #[account(mut)]
    pub voter: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
#[instruction(proposal_id: [u8; 32])]
pub struct RegisterFederation<'info> {
    #[account(seeds = [CONFIG_SEED], bump = config.bump, has_one = admin)]
    pub config: Account<'info, Config>,
    #[account(
        init,
        payer = admin,
        space = Federation::LEN,
        seeds = [FED_SEED, proposal_id.as_ref()],
        bump
    )]
    pub federation: Account<'info, Federation>,
    #[account(mut)]
    pub admin: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
#[instruction(proposal_id: [u8; 32])]
pub struct SetFederationStatus<'info> {
    #[account(seeds = [CONFIG_SEED], bump = config.bump, has_one = admin)]
    pub config: Account<'info, Config>,
    #[account(
        mut,
        seeds = [FED_SEED, proposal_id.as_ref()],
        bump = federation.bump
    )]
    pub federation: Account<'info, Federation>,
    pub admin: Signer<'info>,
}

// ───────────────────────────── Events / Errors ─────────────────────────────
#[event]
pub struct UsagePaid {
    pub payer: Pubkey,
    pub units: u64,
    pub amount: u64,
    pub burned: bool,
    pub memo: [u8; 32],
}

#[event]
pub struct FederationRegistered {
    pub proposal_id: [u8; 32],
    pub mode: u8,
}

#[error_code]
pub enum EcoError {
    #[msg("수치 오버플로우")]
    MathOverflow,
    #[msg("0 금액은 허용되지 않음")]
    ZeroAmount,
    #[msg("스테이크 잔액 부족")]
    InsufficientStake,
    #[msg("권한 없음")]
    Unauthorized,
    #[msg("seed 불일치")]
    SeedMismatch,
    #[msg("잘못된 상태값")]
    BadStatus,
}
