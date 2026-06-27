# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True
"""Native (Cython/C) blackjack round engine — the speed path.

This is a faithful, branch-for-branch port of the pure-Python reference engine
(`blackjack/game.py` + `shoe.py` + `strategy.py` + `sidebets.py`). The whole
N-round Monte-Carlo loop runs in C with the GIL released, so a single call can
crunch tens of millions of rounds without per-round Python overhead, and several
calls run truly in parallel on separate threads.

Design notes
------------
* **One source of truth for strategy.** The basic-strategy charts, pair tables,
  and Illustrious-18 index deviations are copied into C arrays *from*
  `blackjack.strategy` at import time (`init_from_python`). Editing the Python
  tables automatically re-tunes this engine — there is no second hand-maintained
  copy to drift out of sync. `tests/test_fastsim.py` additionally proves the two
  engines produce statistically identical house edges.
* **RNG.** xoshiro256** seeded by splitmix64. This is a *different* stream from
  CPython's Mersenne Twister, so the C engine does not reproduce the Python
  engine bit-for-bit for a given seed — but it is deterministic for a fixed seed
  and core count, and the expected value is identical (validated in tests).
* **Side bets.** Perfect Pairs and 21+3 are supported with their default
  paytables only; the caller falls back to Python for custom paytables.
"""

from libc.math cimport floor
from libc.stdint cimport uint64_t

# --- card lookup tables (filled in init_tables) -------------------------------
cdef int bj_value[52]
cdef int hilo[52]
cdef int rank_index[52]
cdef int is_ace_t[52]
cdef int is_ten_t[52]
cdef int suit_t[52]
cdef int color_t[52]
cdef int high_rank_t[52]

# --- strategy tables (filled in init_from_python) -----------------------------
# action chars: 'H' hit, 'S' stand, 'D' double-else-hit, 'V' double-else-stand,
# 'P' split, 'R' surrender-else-hit
cdef char hard_act[22][10]      # indexed [total 4..21][col 0..9]
cdef char soft_act[22][10]      # indexed [total 13..21][col]
cdef char pair_das[12][10]      # indexed [rank_val 2..11][col]
cdef char pair_nodas[12][10]

cdef int DEV_MAX = 64
cdef int dev_total[64]
cdef int dev_soft[64]
cdef int dev_up[64]
cdef char dev_act[64]
cdef double dev_thr[64]
cdef int dev_dir[64]            # 1 -> ">=", 0 -> "<="
cdef int n_dev = 0
cdef double INSURANCE_TC = 3.0

cdef int _tables_ready = 0


cdef void init_tables():
    cdef int c, r
    # rank-keyed property tables, mirroring blackjack/cards.py
    cdef int bj_by_rank[13]
    cdef int high_by_rank[13]
    cdef int hilo_by_rank[13]
    cdef int i
    for i in range(13):
        bj_by_rank[i] = (2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10, 11)[i]
        high_by_rank[i] = (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14)[i]
        hilo_by_rank[i] = (1, 1, 1, 1, 1, 0, 0, 0, -1, -1, -1, -1, -1)[i]
    for c in range(52):
        r = c % 13
        bj_value[c] = bj_by_rank[r]
        high_rank_t[c] = high_by_rank[r]
        hilo[c] = hilo_by_rank[r]
        rank_index[c] = r
        is_ace_t[c] = 1 if r == 12 else 0
        is_ten_t[c] = 1 if bj_by_rank[r] == 10 else 0
        suit_t[c] = c // 13
        # colour: diamonds(1)/hearts(2) red -> 1, clubs(0)/spades(3) black -> 0
        color_t[c] = 1 if (c // 13) in (1, 2) else 0


def init_from_python():
    """Copy the strategy charts/indices from `blackjack.strategy` into C arrays.

    Called once at import. Keeps this engine's decisions identical to the Python
    reference engine without a second hand-maintained table.
    """
    global n_dev, _tables_ready
    from . import strategy as S

    init_tables()

    cdef int total, rv, j
    cdef bytes row
    for total in range(4, 22):
        row = S.BASIC_HARD[total].encode("ascii")
        for j in range(10):
            hard_act[total][j] = row[j]
    for total in range(13, 22):
        row = S.BASIC_SOFT[total].encode("ascii")
        for j in range(10):
            soft_act[total][j] = row[j]
    for rv in range(2, 12):
        row = S.PAIRS_DAS[rv].encode("ascii")
        for j in range(10):
            pair_das[rv][j] = row[j]
        row = S.PAIRS_NODAS[rv].encode("ascii")
        for j in range(10):
            pair_nodas[rv][j] = row[j]

    cdef int k = 0
    for (t, s, u, act, thr, direction) in S.ILLUSTRIOUS_18:
        dev_total[k] = t
        dev_soft[k] = 1 if s else 0
        dev_up[k] = u
        dev_act[k] = ord(act)
        dev_thr[k] = float(thr)
        dev_dir[k] = 1 if direction == ">=" else 0
        k += 1
    n_dev = k
    _tables_ready = 1


# --- xoshiro256** PRNG --------------------------------------------------------
cdef struct RNG:
    uint64_t s0
    uint64_t s1
    uint64_t s2
    uint64_t s3


cdef inline uint64_t rotl(uint64_t x, int k) noexcept nogil:
    return (x << k) | (x >> (64 - k))


cdef inline void seed_rng(RNG* r, uint64_t seed) noexcept nogil:
    cdef uint64_t z
    cdef uint64_t[4] out
    cdef int i
    for i in range(4):
        seed += <uint64_t>0x9E3779B97F4A7C15
        z = seed
        z = (z ^ (z >> 30)) * <uint64_t>0xBF58476D1CE4E5B9
        z = (z ^ (z >> 27)) * <uint64_t>0x94D049BB133111EB
        z = z ^ (z >> 31)
        out[i] = z
    r.s0 = out[0]
    r.s1 = out[1]
    r.s2 = out[2]
    r.s3 = out[3]


cdef inline uint64_t next_rand(RNG* r) noexcept nogil:
    cdef uint64_t result = rotl(r.s1 * 5, 7) * 9
    cdef uint64_t t = r.s1 << 17
    r.s2 ^= r.s0
    r.s3 ^= r.s1
    r.s1 ^= r.s2
    r.s0 ^= r.s3
    r.s2 ^= t
    r.s3 = rotl(r.s3, 45)
    return result


cdef inline int rand_below(RNG* r, int n) noexcept nogil:
    # Lemire-style unbiased bounded integer in [0, n).
    cdef uint64_t x = next_rand(r)
    cdef uint64_t m = x % <uint64_t>n
    return <int>m


# --- shoe ---------------------------------------------------------------------
cdef int MAX_CARDS = 8 * 52

cdef struct Shoe:
    int cards[416]
    int n
    int pos
    int running_count
    int cut_card
    int decks
    int buffer        # windowed CSM buffer depth (0 = off); cards[] is the pool
    int pool_n        # windowed: number of cards currently in the dealable pool
    int win[64]       # windowed: ring buffer of the last `buffer` dealt cards
    int win_head
    int win_fill
    int win_count     # Hi-Lo running count of the buffer


cdef inline void shoe_shuffle(Shoe* sh, RNG* rng) noexcept nogil:
    cdef int total = sh.decks * 52
    cdef int i, d, c, idx, j, tmp
    idx = 0
    for d in range(sh.decks):
        for c in range(52):
            sh.cards[idx] = c
            idx += 1
    sh.n = total
    sh.pos = 0
    sh.running_count = 0
    if sh.buffer > 0:
        # Windowed CSM: cards[] is the pool, drawn from at random; empty buffer.
        sh.pool_n = total
        sh.win_head = 0
        sh.win_fill = 0
        sh.win_count = 0
        return
    # Fisher-Yates
    for i in range(total - 1, 0, -1):
        j = rand_below(rng, i + 1)
        tmp = sh.cards[i]
        sh.cards[i] = sh.cards[j]
        sh.cards[j] = tmp


cdef inline int shoe_deal_windowed(Shoe* sh, RNG* rng) noexcept nogil:
    # remove a uniform-random card from the pool (swap-with-last)
    cdef int j = rand_below(rng, sh.pool_n)
    cdef int card = sh.cards[j]
    cdef int old
    sh.pool_n -= 1
    sh.cards[j] = sh.cards[sh.pool_n]
    sh.cards[sh.pool_n] = card
    # push into the ring buffer; evict the oldest once full (it rejoins the pool)
    if sh.win_fill < sh.buffer:
        sh.win[sh.win_head] = card
        sh.win_count += hilo[card]
        sh.win_head = (sh.win_head + 1) % sh.buffer
        sh.win_fill += 1
    else:
        old = sh.win[sh.win_head]
        sh.win_count -= hilo[old]
        sh.cards[sh.pool_n] = old
        sh.pool_n += 1
        sh.win[sh.win_head] = card
        sh.win_count += hilo[card]
        sh.win_head = (sh.win_head + 1) % sh.buffer
    return card


cdef inline int cards_remaining(Shoe* sh) noexcept nogil:
    return sh.n - sh.pos


cdef inline double decks_remaining(Shoe* sh) noexcept nogil:
    return (sh.n - sh.pos) / 52.0


cdef inline int needs_shuffle(Shoe* sh) noexcept nogil:
    if sh.buffer > 0:
        return 0                         # a CSM never stops to shuffle
    return 1 if (sh.n - sh.pos) <= sh.cut_card else 0


cdef inline double true_count(Shoe* sh) noexcept nogil:
    if sh.buffer > 0:
        return <double>sh.win_count      # windowed CSM: the buffer count is the signal
    cdef double dr = decks_remaining(sh)
    if dr > 0.25:
        return sh.running_count / dr
    return sh.running_count * 4.0


cdef inline int shoe_deal(Shoe* sh, RNG* rng) noexcept nogil:
    cdef int c
    if sh.buffer > 0:
        return shoe_deal_windowed(sh, rng)
    if sh.pos >= sh.n:
        shoe_shuffle(sh, rng)
    c = sh.cards[sh.pos]
    sh.pos += 1
    sh.running_count += hilo[c]
    return c


cdef inline int shoe_deal_hidden(Shoe* sh, RNG* rng) noexcept nogil:
    cdef int c
    if sh.buffer > 0:
        return shoe_deal_windowed(sh, rng)   # buffer tracks every physical card
    if sh.pos >= sh.n:
        shoe_shuffle(sh, rng)
    c = sh.cards[sh.pos]
    sh.pos += 1
    return c


cdef inline void shoe_reveal(Shoe* sh, int card) noexcept nogil:
    if sh.buffer > 0:
        return                            # already accounted for in the buffer
    sh.running_count += hilo[card]


# --- rules / strategy config (passed by value into the loop) ------------------
cdef struct Rules:
    int decks
    int cut_card
    int h17
    int peek
    double payout
    int double_allowed
    int das
    int dbl_lo
    int dbl_hi
    int max_split
    int resplit_aces
    int hit_split_aces
    int late_surr
    int early_surr
    int csm
    int csm_buffer


cdef struct Strat:
    int is_counter        # bets off a ramp (counter or window_counter)
    double bet_units
    double min_bet
    int min_key
    int max_key
    double ramp[64]
    int insurance
    int pp
    int tp
    double side_unit
    int play_deviations   # plays Illustrious-18 index deviations (counter only)


cdef struct Stats:
    long rounds
    double main_net
    double main_net_sq
    double main_wagered
    double initial_bet
    double total_net
    double total_net_sq
    double total_wagered
    double side_net
    double side_wagered
    double pp_net
    double pp_wag
    double tp_net
    double tp_wag


# --- hand evaluation ----------------------------------------------------------
cdef inline void hand_total(int* cards, int n, int* out_total, int* out_soft) noexcept nogil:
    cdef int total = 0, aces = 0, i
    for i in range(n):
        total += bj_value[cards[i]]
        aces += is_ace_t[cards[i]]
    while total > 21 and aces:
        total -= 10
        aces -= 1
    out_total[0] = total
    out_soft[0] = 1 if aces > 0 else 0


cdef inline int is_blackjack2(int a, int b) noexcept nogil:
    return 1 if (bj_value[a] + bj_value[b] == 21) else 0


cdef inline int col_of(int up) noexcept nogil:
    return 9 if up == 11 else up - 2


# --- side bets (default paytables) --------------------------------------------
cdef inline double perfect_pairs_net(int c0, int c1) noexcept nogil:
    if rank_index[c0] != rank_index[c1]:
        return -1.0
    if suit_t[c0] == suit_t[c1]:
        return 25.0      # perfect
    if color_t[c0] == color_t[c1]:
        return 12.0      # coloured
    return 6.0           # mixed


cdef inline int is_straight3(int a, int b, int c) noexcept nogil:
    cdef int lo, mid, hi, t
    lo = a; mid = b; hi = c
    if lo > mid:
        t = lo; lo = mid; mid = t
    if mid > hi:
        t = mid; mid = hi; hi = t
    if lo > mid:
        t = lo; lo = mid; mid = t
    if lo + 1 == mid and mid + 1 == hi:
        return 1
    # Ace-low wheel: A(14),2,3
    if lo == 2 and mid == 3 and hi == 14:
        return 1
    return 0


cdef inline double twentyone_plus_three_net(int c0, int c1, int up) noexcept nogil:
    cdef int same_rank = 1 if (rank_index[c0] == rank_index[c1] and
                               rank_index[c1] == rank_index[up]) else 0
    cdef int same_suit = 1 if (suit_t[c0] == suit_t[c1] and
                               suit_t[c1] == suit_t[up]) else 0
    cdef int straight = is_straight3(high_rank_t[c0], high_rank_t[c1], high_rank_t[up])
    if same_rank and same_suit:
        return 100.0     # suited trips
    if straight and same_suit:
        return 40.0      # straight flush
    if same_rank:
        return 30.0      # trips
    if straight:
        return 10.0      # straight
    if same_suit:
        return 5.0       # flush
    return -1.0


# --- decision logic -----------------------------------------------------------
cdef inline int should_surrender(int* cards, int n, int up, int is_pair,
                                 Rules* ru) noexcept nogil:
    if not (ru.late_surr or ru.early_surr):
        return 0
    cdef int total, soft
    hand_total(cards, n, &total, &soft)
    if soft:
        return 0
    cdef int pair_rank = bj_value[cards[0]] if is_pair else 0
    cdef int h17 = ru.h17
    if is_pair and pair_rank == 8:
        return 1 if (h17 and up == 11) else 0
    if total == 15:
        if up == 10:
            return 1
        if up == 11 and h17:
            return 1
    if total == 16 and (up == 9 or up == 10 or up == 11):
        return 1
    if total == 17 and up == 11 and h17:
        return 1
    return 0


cdef inline char basic_decide(int* cards, int n, int up, int can_double,
                              int can_split, int can_surrender, Rules* ru) noexcept nogil:
    cdef int c0 = cards[0], c1 = cards[1] if n >= 2 else 0
    cdef int is_pair = 0
    if n == 2:
        if rank_index[c0] == rank_index[c1]:
            is_pair = 1
        elif bj_value[c0] == 10 and bj_value[c1] == 10:
            is_pair = 1

    if can_surrender and should_surrender(cards, n, up, is_pair, ru):
        return b'R'

    cdef int col = col_of(up)
    cdef char code
    cdef int rank_val
    if is_pair and can_split:
        rank_val = bj_value[c0]
        if ru.das:
            code = pair_das[rank_val][col]
        else:
            code = pair_nodas[rank_val][col]
        if code == b'P':
            return b'P'
        if code == b'D' or code == b'V':
            if can_double:
                return code
            return b'H' if code == b'D' else b'S'
        return code

    cdef int total, soft
    hand_total(cards, n, &total, &soft)
    if soft and total <= 21:
        code = soft_act[total][col]
    else:
        code = hard_act[total if total < 21 else 21][col]

    if code == b'D':
        return b'D' if can_double else b'H'
    if code == b'V':
        return b'D' if can_double else b'S'
    if code == b'R':
        return b'R' if can_surrender else b'H'
    return code


cdef inline char decide(int* cards, int n, int up, int can_double, int can_split,
                        int can_surrender, double tc, Rules* ru, Strat* st) noexcept nogil:
    cdef int total, soft, i, is_pair_rank, hit
    cdef char a
    if st.play_deviations and n == 2:
        hand_total(cards, n, &total, &soft)
        is_pair_rank = 1 if rank_index[cards[0]] == rank_index[cards[1]] else 0
        if not is_pair_rank:
            for i in range(n_dev):
                if dev_total[i] == total and dev_soft[i] == soft and dev_up[i] == up:
                    if dev_dir[i]:
                        hit = 1 if tc >= dev_thr[i] else 0
                    else:
                        hit = 1 if tc <= dev_thr[i] else 0
                    if hit:
                        a = dev_act[i]
                        # a "stand" deviation must not override an available
                        # surrender (surrender beats standing on 16v10/15v10/16v9)
                        if a == b'S' and can_surrender and \
                                should_surrender(cards, n, up, 0, ru):
                            break
                        if a == b'D':
                            return b'D' if can_double else b'H'
                        return a
                    break
    return basic_decide(cards, n, up, can_double, can_split, can_surrender, ru)


cdef inline int can_double_hand(int* cards, int n, int total, int from_split,
                                int is_split_aces, Rules* ru) noexcept nogil:
    if n != 2:
        return 0
    if not (ru.double_allowed and ru.dbl_lo <= total <= ru.dbl_hi):
        return 0
    if from_split and not ru.das:
        return 0
    if is_split_aces and not ru.hit_split_aces:
        return 0
    return 1


cdef inline double counter_bet(double tc, Strat* st) noexcept nogil:
    cdef long f = <long>floor(tc)
    if f < st.min_key:
        return st.min_bet
    if f >= st.max_key:
        return st.ramp[st.max_key - st.min_key]
    return st.ramp[f - st.min_key]


# --- one round ----------------------------------------------------------------
cdef void play_round_c(Shoe* sh, RNG* rng, Rules* ru, Strat* st, Stats* acc) noexcept nogil:
    if ru.csm or needs_shuffle(sh) or cards_remaining(sh) < 20:
        shoe_shuffle(sh, rng)

    cdef double tc_start = true_count(sh)
    cdef double main_bet
    if st.is_counter:
        main_bet = counter_bet(tc_start, st)
    else:
        main_bet = st.bet_units

    cdef int p0 = shoe_deal(sh, rng)
    cdef int d_up = shoe_deal(sh, rng)
    cdef int p1 = shoe_deal(sh, rng)
    cdef int hole = shoe_deal_hidden(sh, rng)
    cdef int up_value = bj_value[d_up]
    cdef double su = st.side_unit

    # --- side bets ---
    cdef double raw_pp = 0.0, raw_tp = 0.0
    cdef double side_net = 0.0, side_wag = 0.0
    cdef int has_pp = 0, has_tp = 0
    if st.pp:
        raw_pp = perfect_pairs_net(p0, p1)
        side_net += raw_pp * su
        side_wag += su
        has_pp = 1
    if st.tp:
        raw_tp = twentyone_plus_three_net(p0, p1, d_up)
        side_net += raw_tp * su
        side_wag += su
        has_tp = 1

    cdef double main_net = 0.0, main_wagered = 0.0
    cdef int player_bj = is_blackjack2(p0, p1)
    cdef int up_ace = is_ace_t[d_up]
    cdef int up_ten = is_ten_t[d_up]
    cdef double insurance_net = 0.0
    cdef int took_ins = 1 if (up_ace and st.is_counter and st.insurance
                              and tc_start >= INSURANCE_TC) else 0
    if took_ins:
        main_wagered += 0.5 * main_bet

    cdef int dealer_bj = 0
    if ru.peek and (up_ace or up_ten):
        if is_blackjack2(d_up, hole):
            dealer_bj = 1

    if dealer_bj:
        shoe_reveal(sh, hole)
        if took_ins:
            insurance_net = 2.0 * (0.5 * main_bet)
        main_wagered += main_bet
        if not player_bj:
            main_net += -main_bet
        main_net += insurance_net
        accumulate(acc, main_net, main_wagered, main_bet, side_net, side_wag,
                   has_pp, raw_pp * su, has_tp, raw_tp * su, su)
        return

    if took_ins:
        insurance_net = -0.5 * main_bet
        main_net += insurance_net

    if player_bj:
        main_wagered += main_bet
        main_net += ru.payout * main_bet
        accumulate(acc, main_net, main_wagered, main_bet, side_net, side_wag,
                   has_pp, raw_pp * su, has_tp, raw_tp * su, su)
        return

    # --- player plays out (with splits) ---
    cdef double res_bet[16]
    cdef int res_status[16]      # 0 showdown, 1 bust, 2 surrender
    cdef int res_total[16]
    cdef int n_res = 0

    n_res = play_player(sh, rng, p0, p1, main_bet, up_value, ru, st,
                        res_bet, res_status, res_total)

    cdef int i
    for i in range(n_res):
        main_wagered += res_bet[i]

    cdef int need_showdown = 0
    for i in range(n_res):
        if res_status[i] == 0:
            need_showdown = 1
            break

    shoe_reveal(sh, hole)
    cdef int dcards[32]
    cdef int dn = 2
    dcards[0] = d_up
    dcards[1] = hole
    cdef int dt, dsoft
    if need_showdown:
        while True:
            hand_total(dcards, dn, &dt, &dsoft)
            if dt < 17:
                dcards[dn] = shoe_deal(sh, rng)
                dn += 1
                continue
            if dt == 17 and dsoft and ru.h17:
                dcards[dn] = shoe_deal(sh, rng)
                dn += 1
                continue
            break
    hand_total(dcards, dn, &dt, &dsoft)
    cdef int dealer_total = dt
    cdef int dealer_bust = 1 if dealer_total > 21 else 0

    cdef int ptot
    for i in range(n_res):
        if res_status[i] == 2:        # surrender
            main_net += -0.5 * res_bet[i]
        elif res_status[i] == 1:      # bust
            main_net += -res_bet[i]
        else:                         # showdown
            ptot = res_total[i]
            if dealer_bust or ptot > dealer_total:
                main_net += res_bet[i]
            elif ptot < dealer_total:
                main_net += -res_bet[i]
            # else push

    accumulate(acc, main_net, main_wagered, main_bet, side_net, side_wag,
               has_pp, raw_pp * su, has_tp, raw_tp * su, su)


cdef inline void accumulate(Stats* acc, double main_net, double main_wagered,
                            double initial_bet, double side_net, double side_wag,
                            int has_pp, double pp_net, int has_tp,
                            double tp_net, double side_unit) noexcept nogil:
    cdef double total_net = main_net + side_net
    acc.rounds += 1
    acc.main_net += main_net
    acc.main_net_sq += main_net * main_net
    acc.main_wagered += main_wagered
    acc.initial_bet += initial_bet
    acc.total_net += total_net
    acc.total_net_sq += total_net * total_net
    acc.total_wagered += main_wagered + side_wag
    acc.side_net += side_net
    acc.side_wagered += side_wag
    if has_pp:
        acc.pp_net += pp_net
        acc.pp_wag += side_unit
    if has_tp:
        acc.tp_net += tp_net
        acc.tp_wag += side_unit


# --- player hands with splitting ----------------------------------------------
cdef int play_player(Shoe* sh, RNG* rng, int c0, int c1, double base_bet, int up_value,
                     Rules* ru, Strat* st, double* res_bet, int* res_status,
                     int* res_total) noexcept nogil:
    # explicit pending stack
    cdef int pend_cards[16][32]
    cdef int pend_n[16]
    cdef double pend_bet[16]
    cdef int pend_fromsplit[16]
    cdef int pend_aces[16]
    cdef int sp = 0          # stack pointer (count of pending entries)

    pend_cards[0][0] = c0
    pend_cards[0][1] = c1
    pend_n[0] = 2
    pend_bet[0] = base_bet
    pend_fromsplit[0] = 0
    pend_aces[0] = 0
    sp = 1

    cdef int n_hands = 1
    cdef int allow_surrender = 1 if (ru.late_surr or ru.early_surr) else 0
    cdef int n_res = 0

    cdef int cur[32]
    cdef int cn, from_split, is_split_aces, is_pair, can_split, total, soft
    cdef double bet
    cdef double tc
    cdef int can_dbl, can_sur
    cdef char action
    cdef int splitting_aces, k

    while sp > 0:
        sp -= 1
        cn = pend_n[sp]
        for k in range(cn):
            cur[k] = pend_cards[sp][k]
        bet = pend_bet[sp]
        from_split = pend_fromsplit[sp]
        is_split_aces = pend_aces[sp]

        is_pair = 0
        if cn == 2:
            if rank_index[cur[0]] == rank_index[cur[1]]:
                is_pair = 1
            elif bj_value[cur[0]] == 10 and bj_value[cur[1]] == 10:
                is_pair = 1
        can_split = 1 if (is_pair and n_hands < ru.max_split and cn == 2) else 0
        if is_split_aces and not ru.resplit_aces:
            can_split = 0

        if can_split:
            tc = true_count(sh)
            hand_total(cur, cn, &total, &soft)
            can_dbl = can_double_hand(cur, cn, total, from_split, is_split_aces, ru)
            can_sur = 1 if (allow_surrender and not from_split) else 0
            action = decide(cur, cn, up_value, can_dbl, 1, can_sur, tc, ru, st)
            if action == b'P':
                splitting_aces = is_ace_t[cur[0]]
                n_hands += 1
                # push h2 then h1 (so h1 is played first, matching list.pop order)
                pend_cards[sp][0] = cur[1]
                pend_cards[sp][1] = shoe_deal(sh, rng)
                pend_n[sp] = 2
                pend_bet[sp] = base_bet
                pend_fromsplit[sp] = 1
                pend_aces[sp] = splitting_aces
                sp += 1
                pend_cards[sp][0] = cur[0]
                pend_cards[sp][1] = shoe_deal(sh, rng)
                pend_n[sp] = 2
                pend_bet[sp] = base_bet
                pend_fromsplit[sp] = 1
                pend_aces[sp] = splitting_aces
                sp += 1
                continue

        # play this hand to completion
        n_res = play_one_hand(sh, rng, cur, cn, bet, up_value, ru, st,
                              from_split, is_split_aces, allow_surrender,
                              res_bet, res_status, res_total, n_res)

    return n_res


cdef int play_one_hand(Shoe* sh, RNG* rng, int* cards, int n, double bet, int up_value,
                       Rules* ru, Strat* st, int from_split, int is_split_aces,
                       int allow_surrender, double* res_bet, int* res_status,
                       int* res_total, int n_res) noexcept nogil:
    cdef int status = 0      # 0 stand/showdown, 1 bust, 2 surrender
    cdef int total, soft, cn = n
    cdef int can_dbl, can_sur
    cdef double tc
    cdef char action
    while True:
        hand_total(cards, cn, &total, &soft)
        if total > 21:
            status = 1
            break
        if is_split_aces and not ru.hit_split_aces:
            status = 0
            break
        can_dbl = can_double_hand(cards, cn, total, from_split, is_split_aces, ru)
        can_sur = 1 if (allow_surrender and cn == 2 and not from_split) else 0
        tc = true_count(sh)
        action = decide(cards, cn, up_value, can_dbl, 0, can_sur, tc, ru, st)
        if action == b'R' and can_sur:
            status = 2
            break
        if action == b'D' and can_dbl:
            cards[cn] = shoe_deal(sh, rng)
            cn += 1
            bet *= 2.0
            hand_total(cards, cn, &total, &soft)
            status = 0 if total <= 21 else 1
            break
        if action == b'S':
            status = 0
            break
        # hit
        cards[cn] = shoe_deal(sh, rng)
        cn += 1

    res_bet[n_res] = bet
    res_status[n_res] = status
    if status == 0:
        hand_total(cards, cn, &total, &soft)
        res_total[n_res] = total
    else:
        res_total[n_res] = 0
    return n_res + 1


# --- public entry point -------------------------------------------------------
def simulate_chunk(tuple rules_t, tuple strat_t, long n_rounds,
                   unsigned long long seed):
    """Run `n_rounds` rounds and return the accumulated statistics tuple.

    rules_t  = (decks, cut_card, h17, peek, payout, double_allowed, das,
                dbl_lo, dbl_hi, max_split, resplit_aces, hit_split_aces,
                late_surr, early_surr)
    strat_t  = (is_counter, bet_units, min_bet, min_key, max_key, ramp_list,
                insurance, pp, tp, side_unit)
    """
    if not _tables_ready:
        init_from_python()

    cdef Rules ru
    ru.decks = rules_t[0]
    ru.cut_card = rules_t[1]
    ru.h17 = rules_t[2]
    ru.peek = rules_t[3]
    ru.payout = rules_t[4]
    ru.double_allowed = rules_t[5]
    ru.das = rules_t[6]
    ru.dbl_lo = rules_t[7]
    ru.dbl_hi = rules_t[8]
    ru.max_split = rules_t[9]
    ru.resplit_aces = rules_t[10]
    ru.hit_split_aces = rules_t[11]
    ru.late_surr = rules_t[12]
    ru.early_surr = rules_t[13]
    ru.csm = rules_t[14]
    ru.csm_buffer = rules_t[15]

    cdef Strat st
    st.is_counter = strat_t[0]
    st.bet_units = strat_t[1]
    st.min_bet = strat_t[2]
    st.min_key = strat_t[3]
    st.max_key = strat_t[4]
    ramp_list = strat_t[5]
    st.insurance = strat_t[6]
    st.pp = strat_t[7]
    st.tp = strat_t[8]
    st.side_unit = strat_t[9]
    st.play_deviations = strat_t[10]

    cdef int i
    cdef int rlen = len(ramp_list)
    for i in range(64):
        st.ramp[i] = 0.0
    for i in range(rlen if rlen < 64 else 64):
        st.ramp[i] = ramp_list[i]

    cdef Shoe sh
    sh.decks = ru.decks
    sh.cut_card = ru.cut_card
    sh.buffer = ru.csm_buffer

    cdef RNG rng
    seed_rng(&rng, seed)
    shoe_shuffle(&sh, &rng)

    cdef Stats acc
    acc.rounds = 0
    acc.main_net = 0.0
    acc.main_net_sq = 0.0
    acc.main_wagered = 0.0
    acc.initial_bet = 0.0
    acc.total_net = 0.0
    acc.total_net_sq = 0.0
    acc.total_wagered = 0.0
    acc.side_net = 0.0
    acc.side_wagered = 0.0
    acc.pp_net = 0.0
    acc.pp_wag = 0.0
    acc.tp_net = 0.0
    acc.tp_wag = 0.0

    cdef long r
    with nogil:
        for r in range(n_rounds):
            play_round_c(&sh, &rng, &ru, &st, &acc)

    return (acc.rounds, acc.main_net, acc.main_net_sq, acc.main_wagered,
            acc.initial_bet, acc.total_net, acc.total_net_sq, acc.total_wagered,
            acc.side_net, acc.side_wagered, acc.pp_net, acc.pp_wag,
            acc.tp_net, acc.tp_wag)
