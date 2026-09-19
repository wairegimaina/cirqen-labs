# The Mathematics of Calibration

### How every number on your calibration certificate is arrived at

A calibration certificate carries figures that look self-explanatory and are
not. What does an expanded uncertainty of ±0.98 mmHg mean? Why is the coverage
factor 2.201 rather than 2? Why does a device that passed still show a test
uncertainty ratio of 3.07, and why does that matter?

This handbook answers those questions from first principles. Every equation is
built up from the reasoning behind it rather than quoted, worked numbers
accompany each step, and section 15 carries one complete measurement from five raw
readings through to the line that appears on the certificate. Section 16 is a guide to
reading a certificate critically, including the checks you can do by eye.

It assumes no prior study of measurement theory. If you can add, multiply and
take a square root. You can follow all of it.

---

## 1. What a calibration actually measures

When you calibrate a device, you are not measuring the device. You are measuring
**the difference between what the device says and what is true**.

"What is true" is supplied by a reference standard, a simulator, a calibrator,
a pressure source, whose own accuracy has been established by a higher
laboratory, which in turn was established by a higher one, in an unbroken chain
back to the SI definition of the unit. That chain is what the word
*traceability* means on a certificate.

So a calibration produces three quantities, not one:

| Quantity | Meaning |
|---|---|
| **The value** | The best estimate of what the device reads at this test point |
| **The deviation** | How far that estimate sits from the reference value |
| **The uncertainty** | How much the value itself could be wrong |

The third is the one most often misunderstood, and it is the reason this
handbook exists. An uncertainty is **not** an error. It is not a mistake anyone
made. It is an honest statement of how tightly the measurement pins the answer
down. A measurement without an uncertainty is not a measurement. It is an
opinion with a number attached.

---

## 2. Why we take repeated readings

Apply exactly 200 mmHg to a blood-pressure module five times and you may read:

```
201    202    201    203    202
```

Nothing went wrong. Real instruments scatter, because of electrical noise,
thermal drift, mechanical hysteresis and the finite number of digits on the
display. A single reading cannot tell you where the centre of that scatter lies,
nor how wide the scatter is. Five readings tell you both.

Repeated readings therefore serve two separate purposes, and it is worth keeping
them distinct in your mind:

1. **Averaging** them gives a better estimate of the true indication than any
   one of them.
2. **Their disagreement with each other** is itself a measurement, of how
   repeatable the device is.

The first gives the value. The second gives part of the uncertainty.

---

## 3. The average and the spread

### 3.1 The average

$$ \bar{x} \;=\; \frac{1}{n}\sum_{i=1}^{n} x_i \;=\; \frac{x_1 + x_2 + \cdots + x_n}{n} $$

For our five readings:

$$ \bar{x} \;=\; \frac{201 + 202 + 201 + 203 + 202}{5} \;=\; \frac{1009}{5} \;=\; 201.8 $$

The device reads **201.8 mmHg** when 200 mmHg is applied.

### 3.2 The spread

We need a single number for "how much do these readings disagree?" Start with
how far each reading sits from the average:

```
201 - 201.8 = -0.8
202 - 201.8 = +0.2
201 - 201.8 = -0.8
203 - 201.8 = +1.2
202 - 201.8 = +0.2
```

These sum to zero, they always do, which is what makes the average the
average. So they cannot simply be added. Square them first, which removes the
signs and penalises large departures more than small ones:

```
0.64 + 0.04 + 0.64 + 1.44 + 0.04  =  2.80
```

Now average the squares and take the square root to return to the original
units. This is the **standard deviation**, written `s`:

$$ s \;=\; \sqrt{\frac{1}{n-1}\sum_{i=1}^{n}\left(x_i - \bar{x}\right)^{2}} $$

and for our five readings

$$ s \;=\; \sqrt{\frac{2.80}{4}} \;=\; \sqrt{0.70} \;=\; 0.836660\ \text{mmHg} $$

### 3.3 Why divide by `n - 1` and not `n`

This is the most-asked question in this handbook, and the answer is worth
understanding rather than accepting.

You do not know the true centre of the scatter. You used the average of these
same five readings as a stand-in for it. The average is, by construction, the
point that sits closest to those five readings, closer than the true centre
would. So the squared departures you measured are **slightly too small**, and
dividing by `n` would report a spread slightly tighter than reality.

Dividing by `n - 1` corrects exactly this. The intuition: with five readings you
have five pieces of information, but you spent one of them working out the
average, leaving four to measure the spread. Those four are the *degrees of
freedom*, and they reappear in section 11.

The effect is not small at these sample sizes. With `n = 5`, dividing by 4
instead of 5 makes `s` about 12% larger, 0.8367 rather than 0.7483. Understating
the spread would understate the uncertainty, and every subsequent number would
inherit the error.

---

## 4. Type A: the uncertainty of the average

We report the average, so we need the uncertainty **of the average**, not of a
single reading.

Here is the key insight. If you took another five readings you would get a
slightly different average. Take five readings over and over and the averages
themselves would scatter, but much less than the individual readings do
because averaging cancels noise. How much less? The scatter of the average is
smaller than the scatter of single readings by a factor of the square root of
how many you averaged:

$$ u_A \;=\; \frac{s}{\sqrt{n}} $$

$$ u_A \;=\; \frac{0.836660}{\sqrt{5}} \;=\; \frac{0.836660}{2.236068} \;=\; 0.374166\ \text{mmHg} $$

This is called a **Type A** uncertainty, meaning simply: *evaluated from
repeated observations made during this calibration*. That is all "Type A" means.
It is a statement about where the number came from, not about what kind of
uncertainty it is.

**The practical consequence of the square root.** To halve this component you
need four times the readings, not twice. Going from 3 readings to 5 buys you a
23% reduction; going from 5 to 10 buys a further 29%; going from 10 to 20 buys
only 29% more again. This is why calibration procedures settle at 5 to 10
readings. Beyond that you are spending real time for diminishing returns, and
the other components in section 5 and section 6 come to dominate anyway.

---

## 5. Type B: the uncertainty from the display resolution

Suppose the device displays whole mmHg only. It reads 201. What is the true
indication?

Anything from 200.5 to 201.5 would display as 201. The display has told you the
true value lies in a one-unit-wide window, and, crucially, **nothing more**.
Every value in that window is equally consistent with what you saw.

No amount of repeated reading fixes this. Read it a thousand times and you still
only know the window. This uncertainty must be evaluated from knowledge of the
instrument rather than from the data, which is what makes it **Type B**:
*evaluated by means other than repeated observation*.

### 5.1 Turning a window into a standard uncertainty

To combine this with `u_A` in section 7, we need it expressed the same way `u_A` is, as
a standard deviation. So: what is the standard deviation of a quantity known
only to lie somewhere in a window of width `d`, with no value preferred?

That is a **rectangular** (or uniform) distribution, flat-topped, because every
value is equally likely, and hard-edged, because values outside are impossible.
Its standard deviation is:

$$ u_{\text{res}} \;=\; \frac{d}{\sqrt{12}} $$

$$ u_{\text{res}} \;=\; \frac{1}{\sqrt{12}} \;=\; \frac{1}{3.464102} \;=\; 0.288675\ \text{mmHg} $$

### 5.2 Where the square root of 12 comes from, the derivation

This is worth doing properly, because the constant looks arbitrary and is not.

**The probability square.** The display told us the truth lies somewhere in a
window of width$d$and nothing more. Centre that window on zero, so it runs
from$-a$ to$+a$ with$d = 2a$. Every value in it is equally likely, and no
value outside it is possible.

A probability density must enclose an area of exactly 1: the truth is
*somewhere*. A flat density over a window of width$2a$ is therefore a
rectangle of width$2a$ and height$p$ with

$$ \text{area} \;=\; 2a \cdot p \;=\; 1 \qquad\Longrightarrow\qquad p(x) \;=\; \frac{1}{2a} \quad \text{for } -a \leq x \leq +a $$

and$p(x) = 0$ everywhere else. That rectangle, width$2a$height$1/2a$is
the whole of what the display has told us. Everything below follows from it.

**The mean is the centre.** By symmetry the expected value sits at zero:

$$ \mathbb{E}[X] \;=\; \int_{-a}^{+a} x \cdot \frac{1}{2a}\,dx \;=\; \frac{1}{2a}\left[\frac{x^{2}}{2}\right]_{-a}^{+a} \;=\; \frac{1}{2a}\left(\frac{a^{2}}{2} - \frac{a^{2}}{2}\right) \;=\; 0 $$

which is why the *reading itself* is the best estimate: the rounding is as
likely to have been upward as downward.

**The variance is where the square comes in.** A standard uncertainty is a
standard deviation, and a standard deviation is the square root of the mean
*squared* departure from the centre. Because the mean is zero, that is simply
$\mathbb{E}[X^{2}]$:

$$ \operatorname{Var}(X) \;=\; \mathbb{E}\!\left[X^{2}\right] \;=\; \int_{-a}^{+a} x^{2} \cdot \frac{1}{2a}\,dx $$

The constant$\frac{1}{2a}$ comes outside, and$\int x^{2}\,dx = \frac{x^{3}}{3}$:

$$ \operatorname{Var}(X) \;=\; \frac{1}{2a}\left[\frac{x^{3}}{3}\right]_{-a}^{+a} \;=\; \frac{1}{2a}\left(\frac{a^{3}}{3} - \frac{-a^{3}}{3}\right) \;=\; \frac{1}{2a}\cdot\frac{2a^{3}}{3} \;=\; \frac{a^{2}}{3} $$

Taking the square root gives the standard deviation of the rectangle:

$$ \sigma \;=\; \sqrt{\frac{a^{2}}{3}} \;=\; \frac{a}{\sqrt{3}} $$

**From half-width to full width.** The resolution$d$ is the full step between
displayed values, so$a = d/2$:

$$ u_{\text{res}} \;=\; \frac{a}{\sqrt{3}} \;=\; \frac{d/2}{\sqrt{3}} \;=\; \frac{d}{2\sqrt{3}} \;=\; \frac{d}{\sqrt{4}\cdot\sqrt{3}} \;=\; \frac{d}{\sqrt{12}} $$

So$\sqrt{12}$ is not a magic number. It is$2\sqrt{3}$: the **2** converts full
width to half-width, and the$\sqrt{3}$ belongs to the flat shape. It is what
falls out of integrating$x^{2}$ across a rectangle.

**A sanity check on the size.** The window is$d$ wide, and the uncertainty it
contributes is$d/3.46$a little under a third of the window. That is the
right order: the truth is somewhere in the window, so the typical departure from
the middle should be an appreciable fraction of it, but smaller than the window
itself. A quantity of$d$ would claim the truth is always at an edge; a quantity
of$d/100$ would claim the display is far more informative than it is.

**Why a rectangle and not a bell curve.** A normal distribution has tails
running to infinity, which would assign non-zero probability to values outside
the window. Those values are not merely unlikely. They are  *impossible*. If the
truth were 202.3 the display would have shown 202, not 201. The physics forbids
the tails, so the density must be bounded; and with no reason to prefer the
centre of the window over its edges. It must be flat across it. The rectangle is
not an approximation chosen for convenience. It is what the situation actually
is.

### 5.3 The one thing to get right

`d` is the **full step between adjacent displayed values**, one whole count of
the last digit. A display reading in whole mmHg has `d = 1`. One reading to
0.1 mmHg has `d = 0.1`.

Enter the half-step by mistake and this component comes out half its true size.
If you are unsure, the test is simple: **what is the smallest change in the
input that changes the displayed number?** That is `d`.

---

## 6. Type B: the uncertainty of the reference standard

Your reference standard is not perfect either. Its own certificate states an
uncertainty, and states it, almost always, as an **expanded** uncertainty at a
coverage factor of `k = 2`, because that is the convention for reporting.

You cannot combine an expanded uncertainty with the standard uncertainties from
Section 4 and section 5; they are different kinds of quantity. Divide by the same `k` to bring
it back to standard form:

```
u_ref = U_ref / k
```

If the standard's certificate says `±0.25 mmHg (k = 2)`:

$$ u_{\text{ref}} \;=\; \frac{0.25}{2} \;=\; 0.125\ \text{mmHg} $$

**Check the standard's certificate, every time.** If it quotes a standard
uncertainty rather than an expanded one, do not divide. You would halve a
component that was already correct. If it quotes `k = 3`, divide by 3. The
figure to carry forward is always the *standard* uncertainty.

---

## 7. Combining the components

We now have three standard uncertainties, all in mmHg, all expressed as standard
deviations:

| Component | Symbol | Value | Source |
|---|---|---|---|
| Repeatability | `u_A` | 0.374166 | The five readings |
| Display resolution | `u_res` | 0.288675 | The device's last digit |
| Reference standard | `u_ref` | 0.125000 | The standard's certificate |

### 7.1 Add the squares, not the values

Adding them directly would give 0.787841, and would be wrong, because it
assumes all three effects conspire to push the result the same way at the same
moment. They are independent: the noise in the readings has nothing to do with
where the display happens to round, which has nothing to do with the calibration
of the standard in another laboratory last year.

Independent effects partly cancel. Sometimes one pushes up while another pushes
down. The correct combination adds the **squares** and takes the root, the same
"root sum of squares" that gives the diagonal of a box from its three edges:

$$ u_c \;=\; \sqrt{u_A^{2} + u_{\text{res}}^{2} + u_{\text{ref}}^{2}} $$

```
u_A^2   = 0.374166^2 = 0.140000
u_res^2 = 0.288675^2 = 0.083333
u_ref^2 = 0.125000^2 = 0.015625
                     --------
sum                = 0.238958

u_c = sqrt(0.238958) = 0.488834  mmHg
```

This is the **combined standard uncertainty**, `u_c`. Note it is smaller than
the direct sum (0.7878) and larger than the biggest single component (0.3742)
which is exactly right.

### 7.2 What the squares tell you about where to improve

Because components combine as squares, **the largest one dominates completely.**
Express each as a share of the total:

| Component | Share of `u_c^2` |
|---|---|
| Repeatability | 59% |
| Display resolution | 35% |
| Reference standard | 7% |

Suppose you borrowed a reference standard ten times better, taking `u_ref` from
0.125 to 0.0125. The new combined uncertainty would be 0.4727, an improvement
of 3%. Nearly no gain, for considerable trouble.

Now suppose instead you improved repeatability by a factor of two, better
technique, a settled environment, a warmed-up device, taking `u_A` to 0.1871.
The combined uncertainty falls to 0.3660, a **25% improvement**.

**This is the single most useful thing in this handbook.** Before trying to
improve an uncertainty, square the components and find the big one. Effort spent
on anything else is close to wasted. And note that the resolution component
cannot be improved at all without a device that displays more digits, which is
often the real ceiling on what a calibration can achieve.

### 7.3 What this combination assumes

Two assumptions are built in, and both are reasonable here, but you should know
they are there.

**Independence.** The components must not be linked. If they were, if, say, the
same temperature error affected both the standard and the device, the
cancellation would not occur and additional terms would be required.

**Common units and direct effect.** Every component above is already in mmHg and
affects the reading one-for-one. An influence that acts *indirectly*, an ambient
temperature error affecting a pressure reading through the device's temperature
coefficient, must first be converted into mmHg by multiplying by that
coefficient before it can join the sum. That multiplier is called a *sensitivity
coefficient*. Where all components are already in the measurand's own units, as
here, every sensitivity coefficient is 1 and the arithmetic simplifies to what
you see above.

---

## 8. Expanded uncertainty and the coverage factor

`u_c` is one standard deviation. For a bell-shaped distribution that spans only
about 68% of the plausible range, roughly a one-in-three chance the truth lies
outside. Too weak a statement for a certificate.

Multiply by a **coverage factor** `k` to widen the interval:

$$ U \;=\; k \cdot u_c $$

$$ U \;=\; 2 \times 0.488834 \;=\; 0.977668\ \text{mmHg} $$

The result reads: **the device indicates 201.8 mmHg ± 0.98 mmHg**, and we expect
the true indication to lie in that interval about 95% of the time.

| `k` | Approximate coverage |
|---|---|
| 1 | 68% |
| 1.645 | 90% |
| 1.96 | 95% |
| **2** | **95.45%** |
| 2.576 | 99% |
| 3 | 99.73% |

`k = 2` is the near-universal convention, and the reason is practical rather
than mathematical: it is close enough to 95% to serve, and it makes every
laboratory's certificates directly comparable. Whatever value is used **must be
stated on the certificate**, because `± 0.98` means nothing without it.

**What this system does.** It does not assume `k = 2`. It derives the coverage
factor from the effective degrees of freedom (section 11) and treats the configured
value as a floor, so `k` is never smaller than the laboratory has specified but
widens when the sample is small. For the five readings above the derived factor
is **2.201**, giving

```
U = 2.201 x 0.488834 = 1.075924  mmHg
```

rather than the 0.977668 that `k = 2` would give, a 10% wider and more honest
interval. The factor actually applied is printed in the `k` column, and the
notes state that it was derived. Where you see a `k` above 2 on a certificate,
that is the sample size being accounted for, not an error.

---

## 9. Deviation from nominal, and the tolerance test

### 9.1 The deviation

The reference applied 200. The device indicated 201.8. The gap is:

```
201.8 - 200.0 = +1.8  mmHg
```

The device reads **1.8 mmHg high**.

**Read the sign convention on your certificate before interpreting this
column.** Two conventions are in use and they differ only in sign:

| Convention | Formula | For this measurement | Meaning of a positive value |
|---|---|---|---|
| **Error of indication** | indication - reference | +1.8 | Device reads **high** |
| **Correction** | reference - indication | -1.8 | Add this to the reading to fix it |

Both are correct and both are used in practice. They are exact negatives of each
other, so a column labelled with one and computed as the other inverts the
meaning of every row. If the certificate's heading does not make the convention
explicit, establish it from a point where you know which way the device leans
before trusting the column.

### 9.2 The tolerance test

The tolerance is the largest deviation the device is permitted. For this
parameter it is ±3 mmHg:

```
|+1.8| = 1.8  <=  3  ->  PASS
```

### 9.3 What that PASS does, and does not, account for

This is the most consequential point in the handbook for anyone reading a
certificate.

The tolerance test above compares the deviation with the tolerance **and takes
no account of the uncertainty**, even though the uncertainty was just computed
and appears on the same page.

Consider a deviation of 2.9 mmHg against a tolerance of 3.0, with an uncertainty
of ±0.98. The deviation is inside tolerance, so the test passes. But the true
deviation could lie anywhere from about 1.9 to 3.9, and a good part of that
range is **outside** tolerance. The honest verdict is not "pass". It is "too
close to call with this measurement".

Two standard tools address this, and section 10 and section 11 cover them. When a certificate
shows a simple pass/fail against tolerance, understand what you are being told:
**the measured deviation was inside the limit.** Not: the device is inside the
limit.

---

## 10. Two checks that make a verdict trustworthy

### 10.1 Is the measurement good enough to judge the device?

Compare the tolerance you are judging against with the uncertainty you are
judging with. The ratio is the **test uncertainty ratio**:

```
TUR = tolerance / U
```

```
TUR = 3.0 / 0.977668 = 3.07
```

The long-established requirement is **TUR of at least 4**. At 4:1 the
measurement is sharp enough relative to the tolerance that a simple pass/fail
carries a false-accept risk of well under one percent, and the uncertainty can
reasonably be set aside.

Our 3.07 falls short. The measurement is not quite good enough to judge this
tolerance by simple comparison, and section 7.2 already showed where to look: 59% of
the uncertainty is repeatability, so more readings or steadier technique is the
route to a defensible verdict.

**A low TUR does not mean the calibration is invalid.** It means a bare
pass/fail overstates what you know, and the guard band below should be applied
instead.

### 10.2 Guard banding: shrinking the limit to absorb the doubt

If the measurement cannot resolve the tolerance cleanly, the remedy is to accept
only within a **reduced** limit. Pull the acceptance limit in by the
uncertainty:

```
acceptance limit = tolerance - U
```

```
acceptance limit = 3.0 - 0.977668 = 2.022  mmHg
```

Now:

- deviation <= 2.022 -> **pass**, and the uncertainty cannot change that
- deviation >= 3.978 (tolerance + U) -> **fail**, and the uncertainty cannot save it
- between the two -> **indeterminate**: the measurement cannot decide

Our +1.8 sits below 2.022, so it passes *with the uncertainty accounted for*,
a materially stronger statement than section 9.2 made.

That third outcome is the point. A guarded decision has **three** possible
results, not two, and refusing to produce a verdict when the data does not
support one is a feature. A device landing in the indeterminate band is not
condemned; it needs a better measurement, or a decision recorded by someone with
the authority to accept the risk.

Guard banding always trades one risk for another: you reduce the chance of
passing a bad device and increase the chance of failing a good one. Which matters
more is a clinical and commercial judgement, not a mathematical one, which is
why the rule in force must be **stated on the certificate** rather than left
implicit.

**What this system does.** Guard banding is applied, and the `Status` column
reports `PASS`, `FAIL` or `INDETERMINATE` accordingly. The rule is printed in
the certificate notes, as ISO/IEC 17025 requires of any document carrying a
statement of conformity. The test uncertainty ratio is printed for every test
point, and any parameter falling below 4:1 is named in a measurement-capability
note rather than left for the reader to spot.

---

## 11. Small samples: when `k = 2` is not enough

Section 8 used `k = 2` for about 95% coverage. That value assumes you know the
spread well. With five readings you do not, `s` was itself estimated from four
degrees of freedom (section 3.3), and it could easily be too small.

For small samples the correct multiplier comes from Student's `t` distribution
which widens as the sample shrinks:

| Readings `n` | Degrees of freedom | `t` for 95% |
|---|---|---|
| 3 | 2 | 4.30 |
| 5 | 4 | 2.78 |
| 10 | 9 | 2.26 |
| 20 | 19 | 2.09 |
| Large |, | 1.96 |

At `n = 5` the honest multiplier for 95% coverage is **2.78**, not 2. Using 2
understates the expanded uncertainty by roughly 40%.

The full treatment is not quite that severe, because only the Type A component
carries few degrees of freedom, the resolution and reference components are
effectively known, and blending all three (by a standard calculation called the
Welch-Satterthwaite formula) yields an *effective* degrees of freedom higher than
4, and so a multiplier between 2 and 2.78. The correction is real but smaller
than the table alone suggests.

**What this system does.** It computes the effective degrees of freedom by the
Welch-Satterthwaite formula and looks up `t` at 95% for that value, then uses
whichever is larger: that factor, or the one configured on the parameter. For
the worked example the effective degrees of freedom is about 11.7, higher than
the 4 that Type A alone would give, because resolution and the reference
standard make up 41% of the budget, and the factor is 2.201.

The practical guidance:

- With **10 or more** readings the derived factor sits close to 2.
- With **3 to 5** readings it will visibly exceed 2. That is correct, not a
  fault.
- With **2 readings** there is one degree of freedom and `t` is **12.706**. A
  certificate showing a factor that large is telling you the measurement rests
  on two observations and cannot support a 95% statement. Treat it as a
  prompt to repeat the measurement with more readings, not as a result.
- **Never** compare an uncertainty computed at `k = 2` with one computed from
  `t` as though they were the same quantity.

---

## 12. Linearity: is the device consistent across its range

A device can be accurate at 200 mmHg and poor at 50. Calibrating at several test
points across the range reveals the shape of the relationship, not just one
point on it.

Plot reference value (horizontal) against indicated value (vertical). A perfect
device gives a straight line at 45°. Real devices give something near a straight
line, and the informative question is **how near**.

Fit the best straight line through the points by least squares, the line
minimising the total squared vertical distance to the points:

```
             n | sum(xy) - sum(x) | sum(y)
slope     =  -----------------------------
                n | sum(x^2) - (sum(x))^2

intercept =  ( sum(y) - slope | sum(x) ) / n
```

Then for each test point, the **residual** is how far the actual reading sits
from that fitted line:

```
residual = indicated - ( slope | reference + intercept )
```

The largest residual, usually expressed as a percentage of full scale, is the
**linearity error**.

Slope, intercept and residuals each say something different, and separating them
is the value of the exercise:

| Feature | What it indicates | Typical remedy |
|---|---|---|
| Intercept away from zero | A constant offset across the whole range | Zero adjustment |
| Slope away from 1 | A proportional (gain) error | Span adjustment |
| Large residuals | Genuine non-linearity | Not adjustable; a limit on the device |

**What this system does.** The certificate prints slope, intercept, the largest
deviation from the fitted line and that deviation as a percentage of span, for
every parameter with two or more test points. The chart shows each parameter
against its fitted line with a residual panel beneath, because at certificate
scale a small non-linearity on a 45-degree line is invisible without one.

This is why linearity matters practically. Offset and gain errors are usually
correctable at the device. A large residual is not. It is a characteristic of
the instrument, and it tells you the device cannot be trusted to interpolate
between the points you tested.

---

## 13. Drift: how the device behaves over years

One calibration is a snapshot. Several, over time, show a trajectory, and the
trajectory is what determines how often the device actually needs calibrating.

Fit a straight line to deviation against elapsed time, using the same least
squares arithmetic as section 12 with days on the horizontal axis. The slope is the
**drift rate**:

```
drift per year = slope (deviation units per day) × 365
```

### 13.1 How well does the line fit

A slope alone can mislead. Two or three noisy points will always support *some*
line. The companion figure is `R^2`, which reports how much of the movement the
straight line actually explains:

```
        sum of squared residuals from the line
R^2 = 1 - --------------------------------------
         sum of squared departures from the mean
```

| `R^2` | Reading |
|---|---|
| Above 0.9 | A clear, steady trend, genuine drift |
| 0.5 to 0.9 | A trend with real scatter, plausible, watch it |
| Below 0.5 | Mostly noise, do not plan around this slope |

**Always read the drift rate and `R^2` together.** A large drift rate with `R^2`
of 0.2 is not drift; it is two scattered points and a line drawn through them.
With only two calibrations `R^2` is 1.0 by construction, the line passes exactly
through both, and means nothing at all. Drift becomes real evidence at three
points and persuasive at five.

### 13.2 Judging a drift rate: always against the tolerance

A drift rate is a dimensioned quantity, and a bare number carries no meaning
across parameters. Is 0.4 per year large? For a pressure channel with a ±3 mmHg
tolerance it is modest. For an electrical safety leakage-current limit it could
be enormous. For an infusion pump in mL/min it is negligible.

The only comparison that transfers between parameters is **against that
parameter's own tolerance**:

```
drift per year as a fraction of tolerance = |drift per year| / tolerance
```

| Fraction of tolerance per year | Reading | Interval implication |
|---|---|---|
| Under 0.1 | Very stable | Interval can likely be extended |
| 0.1 to 0.25 | Normal | Current interval appropriate |
| 0.25 to 0.5 | Drifting noticeably | Consider shortening |
| Over 0.5 | Will breach within two years | Shorten now |

Treat any stability grade expressed as a bare number, with no reference to the
tolerance or the unit, with caution. It cannot be comparing like with like
across different parameters.

**What this system does.** Stability is graded on the fraction of tolerance, and
that fraction is printed beside the drift rate so the grade can be checked. The
grades are **Very stable**, **Normal**, **Drifting** and **Urgent** on the bands
above, plus **Ungraded** where no tolerance is on record, in that case no
interval advice is given, rather than a grade being guessed. The overall figure
is the worst parameter's, not an average, because averaging hides the one
parameter approaching its limit.

### 13.3 From drift rate to calibration interval

Drift is what makes a calibration interval a calculable quantity rather than a
convention. If a device starts within tolerance and drifts steadily, the time
until it reaches the limit is:

```
                  remaining margin to tolerance
time to breach =  -----------------------------
                     drift rate per year
```

A device 1.8 mmHg from a 3.0 mmHg limit, drifting 0.4 mmHg/year, has
`1.2 / 0.4 = 3 years` before it breaches. A calibration interval of 12 months
carries comfortable margin; one of 4 years does not.

This is how a measured interval beats a conventional one in both directions, it
justifies extending the interval on stable devices and shortening it on the few
that need it, instead of treating every device the same.

---

## 14. Suspect readings

Occasionally one reading in a set is plainly unlike the others:

```
201    202    248    203    202
```

That 248 is not scatter. Something happened, a disconnected lead, a transcribed
digit, a bump.

The obvious test would be "how many standard deviations from the mean is it?"
and it fails, because the outlier inflates both the mean and the standard
deviation it is being tested against. A single bad value hides itself.

So use measures a stray value cannot distort:

- the **median**, the middle value when sorted, which ignores how extreme the
  extremes are;
- the **median absolute deviation** (MAD), the median of the distances from the
  median.

```
sorted:                 201  202  202  203  248
median:                 202
distances from median:    1    0    0    1   46
sorted distances:         0    0    1    1   46
MAD:                      1
```

The MAD is 1, completely unmoved by the 248. Now score each reading:

```
                 0.6745 | (reading - median)
modified Z  =    ---------------------------
                            MAD
```

```
for 248:  0.6745 × (248 - 202) / 1  =  31.0
for 203:  0.6745 × (203 - 202) / 1  =   0.67
```

A reading is flagged when its score exceeds **3.5**. The 248 scores 31 and is
flagged decisively; everything else scores below 1.

The 0.6745 simply rescales MAD so that, for well-behaved data, the score matches
the familiar "number of standard deviations".

**A flag is not a licence to delete.** Investigate and record. Discard a reading
only when you can identify the physical cause, and note the discard on the
record. Silently deleting inconvenient readings is the most direct way to make a
calibration record worthless.

One caution specific to coarse displays: if more than half the readings are
identical, common when the resolution is coarse relative to the noise, the MAD
is exactly zero and the score cannot be computed. Outlier detection is simply
unavailable in that case, which is the safe behaviour.

---

## 15. One measurement, start to finish

**Parameter:** non-invasive blood pressure, systolic
**Test point:** 200 mmHg | **Tolerance:** ±3 mmHg
**Reference standard:** ±0.25 mmHg at `k = 2`
**Device resolution:** 1 mmHg | **Coverage factor:** `k = 2`
**Readings:** 201, 202, 201, 203, 202

```
STEP 1, Average
    x_bar = 1009 / 5                                  = 201.800000 mmHg

STEP 2, Standard deviation
    squared departures  = 0.64+0.04+0.64+1.44+0.04    =   2.800000
    divide by n-1 = 4                                 =   0.700000
    s = sqrt(0.700000)                                =   0.836660 mmHg

STEP 3, Type A, repeatability
    u_A = 0.836660 / sqrt(5)                          =   0.374166 mmHg

STEP 4, Type B, resolution
    u_res = 1 / sqrt(12)                              =   0.288675 mmHg

STEP 5, Type B, reference standard
    u_ref = 0.25 / 2                                  =   0.125000 mmHg

STEP 6, Combine in quadrature
    u_A^2   = 0.140000
    u_res^2 = 0.083333
    u_ref^2 = 0.015625
    total  = 0.238958
    u_c = sqrt(0.238958)                              =   0.488834 mmHg

STEP 7, Expand
    U = 2 x 0.488834                                  =   0.977668 mmHg

STEP 8, Deviation from nominal
    201.800000 - 200.000000                           =  +1.800000 mmHg

STEP 9, Simple tolerance test
    |+1.8| = 1.8 <= 3.0                               ->  PASS

STEP 10, Measurement capability
    TUR = 3.0 / 0.977668                              =   3.07   (below 4)

STEP 11, Guarded decision
    acceptance limit = 3.0 - 0.977668                 =   2.022 mmHg
    1.8 <= 2.022                                      ->  PASS (guarded)
```

**Reported result**

> At an applied 200 mmHg the device indicated **201.8 mmHg**, a deviation of
> **+1.8 mmHg** (reading high), with an expanded uncertainty of **±0.98 mmHg**
> (`k = 2`, approximately 95%), from `n = 5` readings. The deviation is within
> the ±3 mmHg tolerance both directly and after guard banding: **PASS**. The test
> uncertainty ratio is 3.07, below the customary 4:1, the dominant contribution
> being repeatability at 59% of the uncertainty budget.

That final sentence is what distinguishes a metrological report from a number.
It states the verdict, the rule used to reach it, the confidence, and the
limitation.

---

## 16. How to read your certificate

### 16.1 The results table

| Column | What it is | What to check |
|---|---|---|
| Set value | Reference applied | Points span the range you actually use |
| Mean | Average of the readings |, |
| Std dev | Spread of the readings | Small relative to tolerance; if not, repeatability is poor |
| Error | Deviation of the mean from the reference | **Confirm the sign convention (section 9.1)** |
| Tolerance | Permitted deviation | Matches the specification you intend to hold the device to |
| TUR | Tolerance divided by the expanded uncertainty | 4:1 or better; below that see section 10.1 |
| Status | `PASS`, `FAIL` or `INDETERMINATE` | Guard banded, per the rule in the notes |

### 16.2 The uncertainty table

| Column | What it is |
|---|---|
| Set value | The test point |
| `n` | How many readings the mean and Type A came from |
| Type A | From the repeated readings, repeatability |
| Type B | From the display resolution |
| Reference | From the reference standard's own certificate |
| Combined | Root sum of squares of the three components |
| Expanded | Combined × `k`, the figure to quote |
| `k` | The coverage factor **actually applied**, derived per section 11 |

**Two arithmetic checks you can do by eye.**

First, the components must reconcile. Square Type A, Type B and Reference, add
them, take the root: you should recover Combined. If a component is missing from
the printed table the arithmetic will not close, and you should ask for the
missing column before relying on the budget, a budget that does not add up
cannot be checked by anyone.

Second, Expanded should equal Combined × `k`. If the notes assert `k = 2` while
the `k` column shows something else, one of the two is wrong.

### 16.3 Five questions worth asking about any certificate

1. **How many readings?** The uncertainty depends on `n`, and `n = 2` and
   `n = 10` are not comparable measurements. If `n` is not stated, the Type A
   component cannot be checked.
2. **Which `k`, and is it appropriate for `n`?** `k = 2` with three readings is
   optimistic (section 11).
3. **Which decision rule?** Simple acceptance, or guard banded? They give
   different answers on marginal points, and a certificate issuing a statement
   of conformity should say which was applied.
4. **What is the TUR?** Below 4:1, a bare pass on a marginal point is a weaker
   statement than it appears.
5. **Is the certificate number unique and permanent?** The number is the
   document's identity. It should be issued once, never reissued to a different
   device, and never renumbered afterwards. If you can find two certificates
   sharing a number, or a filed certificate whose number no longer matches the
   record, the traceability chain in section 1 is broken no matter how good the
   measurement was.

A certificate that answers all five is one you can defend. A certificate that
answers none may still record a perfectly good measurement, but you cannot tell
from the certificate, and neither can an assessor.

**Where this system stands.** Questions 1 to 4 are now answered on the page: `n`
is printed per test point, the `k` column carries the factor actually applied
and the notes say it was derived from the effective degrees of freedom, the
decision rule is stated in full, and the TUR appears per point with a named
warning below 4:1. Question 5, whether the number is unique and permanent, is
answered for issuance: numbers are allocated by one authority and never
invented locally. It is **not** yet answered for tamper-evidence; the QR code
does not yet carry a verifiable signature, so treat the printed number as a
reference rather than as proof of authenticity.

---

## 17. Notation and glossary

| Symbol | Name | Meaning |
|---|---|---|
| `n` | Sample size | Number of repeated readings |
| `x_bar` | Mean | Average of the readings |
| `s` | Standard deviation | Spread of the readings about their mean |
| `u_A` | Type A standard uncertainty | Uncertainty of the mean, from repeatability |
| `u_res` | Resolution component | From the finite display step |
| `u_ref` | Reference component | From the reference standard's certificate |
| `u_c` | Combined standard uncertainty | Root sum of squares of all components |
| `U` | Expanded uncertainty | `k | u_c`, the figure quoted on certificates |
| `k` | Coverage factor | Multiplier setting the confidence level |
| `d` | Resolution | Smallest step between displayed values |
| `T` | Tolerance | Largest permitted deviation |
| `R^2` | Coefficient of determination | How well a fitted line explains the data |
| TUR | Test uncertainty ratio | `T / U`, is the measurement good enough |

**Standard uncertainty**, an uncertainty expressed as one standard deviation.
The form in which components must be expressed before they can be combined.

**Expanded uncertainty**, a standard uncertainty multiplied by `k` to give a
stated confidence. The form in which uncertainty is reported.

**Type A / Type B**, how a component was *evaluated*: Type A from repeated
observations in this calibration, Type B by any other means (a specification, a
certificate, the resolution, judgement). The labels describe provenance only
and neither is inherently more reliable.

**Traceability**, an unbroken, documented chain of calibrations linking this
measurement to the SI definition of the unit, each link carrying its own stated
uncertainty.

**Degrees of freedom**, how many independent pieces of information contributed
to an estimate. `n - 1` for a standard deviation from `n` readings.

**Guard band**, the amount by which an acceptance limit is pulled inside the
tolerance to absorb measurement uncertainty.

---

## 18. Where this mathematics comes from

None of it is local practice. It is the international consensus, and these are
the documents that define it:

- **JCGM 100:2008**, *Evaluation of measurement data, Guide to the expression
  of uncertainty in measurement* (the "GUM"). The authoritative source for
  everything in section 3 to section 8 and section 11. Freely available from the BIPM.
- **ISO/IEC 17025:2017**, *General requirements for the competence of testing
  and calibration laboratories*. Clause 7.8.6 requires that where a statement of
  conformity is issued, the decision rule used be documented and reported, the
  requirement behind section 10.2 and section 16.3.
- **ILAC-G8:2019**, *Guidelines on decision rules and statements of
  conformity*. Guard banding and conformity decisions, section 10.2.
- **JCGM 200:2012**, the *International vocabulary of metrology* (VIM). Defines
  error, correction, uncertainty and traceability, and settles the terminology
  in section 9.1.

A calibration performed and reported along these lines is defensible anywhere in
the world, because these are the same rules everywhere in the world.

---

*This handbook describes the mathematics of the calibration measurement. Worked
values are exact and may be reproduced by hand as a check.*
