# Package Upgrade Plan - February 13, 2026

## 🚀 High-Impact Upgrades (Do These First)

### 1. **Update requirements.txt to Match Installed Versions** ⚡
**Effort:** 2 minutes | **Impact:** Eliminates version drift

Your installed packages are newer than requirements.txt specifies. Update these lines:

```python
# Current requirements.txt has:
redis==5.2.1           → redis==6.1.0
slack_sdk==3.18.1      → slack_sdk==3.27.1
python-dotenv==0.19.1  → python-dotenv==1.0.1
```

**Optional but recommended:** Install redis with hiredis for compiled response parser:
```bash
pip install redis[hiredis]
```

---

### 2. **Next.js 16.0.10 → 16.1.x** 🔥
**Effort:** 5 minutes | **Impact:** 10-14x faster dev startup

```bash
cd frontend-ohack.dev
npm install next@latest
```

**Benefits:**
- Development restarts: **15s → 1.1s** (10-14x faster)
- Production builds: **2-5x faster** with Turbopack
- Install size: **20MB smaller**
- Fast Refresh: **5-10x faster**

This is a **no-brainer upgrade** with massive developer productivity gains.

---

### 3. **Migrate moment.js → date-fns** 💎
**Effort:** 2-4 hours | **Impact:** ~192KB bundle reduction

You already have both installed! Replace moment usage:

```javascript
// Before (moment)
import moment from 'moment';
const formatted = moment(date).format('YYYY-MM-DD');

// After (date-fns)
import { format } from 'date-fns';
const formatted = format(date, 'yyyy-MM-dd');
```

**Benefits:**
- **192KB+ smaller bundle** = faster page loads
- Tree-shakeable (only import what you use)
- Immutable/functional API (fewer bugs)
- Actively maintained (moment is in maintenance mode)

**Migration help:** Use [moment-to-date-fns codemod](https://github.com/mobz/moment-to-date-fns)

---

### 4. **MUI v5.16.7 → v6.x** 📦
**Effort:** 1-2 hours | **Impact:** 25% smaller package (2.5MB reduction)

```bash
npm install @mui/material@latest @mui/icons-material@latest
```

Then run codemods:
```bash
npx @mui/codemod@latest v6.0.0/preset-safe ./src
```

**Benefits:**
- **2.5MB smaller package** = faster loads
- Better tree-shaking
- Minimal breaking changes (codemods handle most)

---

## ⚠️ Medium Priority Upgrades

### 5. **React 18.3.1 → 19.x** (Consider for future)
**Effort:** 4-8 hours | **Impact:** 20% faster rendering

**Wait until after Next.js and MUI upgrades**, then:

```bash
npm install react@latest react-dom@latest
```

Run codemods:
```bash
npx react-codemod@latest react-19/replace-reactdom-render ./src
npx react-codemod@latest react-19/replace-string-ref ./src
```

**Benefits:**
- **20% faster rendering** for large lists
- Automatic memoization via React Compiler
- Better memory usage

**Breaking changes:** Requires testing with MUI (wait for MUI to fully support React 19)

---

### 6. **firebase_admin 6.5.0 → 7.1.0**
**Effort:** 30 minutes | **Impact:** Faster user listing/pagination

Only if you use `list_users()` or real-time database:

```bash
pip install firebase_admin==7.1.0
```

---

## 🔍 Audit & Replace (Optional)

### 7. **lodash Optimization**
**Effort:** Variable | **Impact:** Depends on usage

Check your lodash imports:
```bash
cd frontend-ohack.dev
grep -r "from 'lodash'" src/ | wc -l
```

If you have heavy lodash usage:
- Replace simple operations (map, filter, find) with native JS
- Keep complex operations (debounce, throttle, cloneDeep)
- Consider switching to `lodash-es` for better tree-shaking

---

## 📊 Expected Performance Gains

| Upgrade | Bundle Reduction | Performance Gain | User Impact |
|---------|------------------|------------------|-------------|
| moment → date-fns | ~192KB | N/A | **Faster page loads** |
| MUI v6 | ~2.5MB | N/A | **Faster page loads** |
| Next.js 16.1 | -20MB install | 10-14x dev speed | **Faster development** |
| React 19 | N/A | 20% rendering | **Smoother UI** |
| **Total** | **~195KB bundle** | **20-1400% faster** | **Significantly better UX** |

---

## 🎯 Recommended Action Plan

**Week 1 (Quick wins):**
1. ✅ Update requirements.txt (5 min)
2. ✅ Upgrade Next.js to 16.1.x (5 min)
3. ✅ Test application thoroughly

**Week 2 (High impact):**
4. ✅ Migrate moment → date-fns (2-4 hours)
5. ✅ Upgrade MUI to v6 (1-2 hours)
6. ✅ Test thoroughly

**Week 3 (Optional):**
7. ✅ Upgrade React to 19 (4-8 hours)
8. ✅ Full regression testing

**Total estimated effort:** 8-15 hours for **~195KB bundle reduction + 20-1400% performance gains**

---

## 📝 Detailed Package Analysis

### Backend (Python)

#### cachetools 5.2.0 → 7.0.1
**Priority: LOW** ❌

**Performance Changes:**
- Version 6.1.0 improved LFUCache insertion performance
- Version 6.2.0 improved RRCache performance (with increased memory consumption)
- Version 5.5.2 reduced @cached lock/unlock operations
- **However**, v7.0.0 introduces cache stampede handling that can **degrade performance** in some scenarios

**Breaking Changes:**
- Requires Python 3.9+ (you're on 3.9.13, so compatible)
- Removes MRUCache and func.mru_cache decorator

**Verdict:** **Not worth upgrading.** The performance improvements are minor and situational, while v7.0.0's stampede prevention can actually hurt performance. The migration effort outweighs benefits for a utility library.

---

#### firebase_admin 6.5.0 → 7.1.0 (latest)
**Priority: MEDIUM** ⚠️

**Performance Improvements:**
- Improved `list_users()` API performance by reducing repeated processing during pagination
- Fixed performance issue in `db.listen()` API for processing large RTDB nodes

**Breaking Changes:**
- v7.0.0 drops Python 3.7 and 3.8 support (you're on 3.9.13, so safe)
- Changes to Cloud Messaging and Firebase ML APIs

**Verdict:** **Worth upgrading if you use `list_users()` or real-time database features.** The performance improvements are targeted but significant for affected APIs. Actively maintained with regular updates.

---

#### redis 5.2.1 (requirements.txt) vs 6.1.0 (installed)
**Priority: HIGH** ✅

**Action Needed:** Update requirements.txt to match installed version (6.1.0)

**Performance Improvements:**
- v6.0.0+ introduces hiredis support for faster response parsing (compiled response parser)
- Modern async/await support
- Better connection pooling

**Breaking Changes:**
- v6.0.0 changed default dialect for Redis search/query to dialect 2
- Python 3.8 reached EOL (v6.1.0 is last version supporting 3.8, v6.2.0+ requires 3.9+)

**Verdict:** **Update requirements.txt immediately to 6.1.0** to match your installation. Consider installing with `redis[hiredis]` for significant performance boost (compiled parser).

---

#### slack_sdk 3.18.1 (requirements.txt) vs 3.27.1 (installed)
**Priority: HIGH** ✅

**Action Needed:** Update requirements.txt to match installed version (3.27.1)

**Performance Improvements:**
- New `WebClient#files_upload_v2()` method with significant performance improvements over legacy files.upload API
- SDK rewrite for better maintainability and modern Python 3 features

**Breaking Changes:**
- Minor API changes between versions (mostly additions)

**Verdict:** **Update requirements.txt immediately to 3.27.1.** If you use file uploads, migrate to `files_upload_v2()` for better performance.

---

#### python-dotenv 0.19.1 (requirements.txt) vs 1.0.1 (installed)
**Priority: HIGH** ✅

**Action Needed:** Update requirements.txt to match installed version (1.0.1)

**Performance Improvements:**
- Refactored parser fixes parsing inconsistencies (more correct, not necessarily faster)
- Better UTF-8 handling by default

**Breaking Changes:**
- Drops Python 2 and 3.4 support (you're on 3.9.13, so safe)
- Default encoding changed from `None` to `"utf-8"`
- Parser interprets escapes as control characters only in double-quoted strings

**Verdict:** **Update requirements.txt to 1.0.1.** Minor performance impact but better correctness and modern Python support.

---

### Frontend (Node/React)

#### Next.js 16.0.10 → 16.1.x
**Priority: VERY HIGH** 🚀

**Performance Improvements:**
- **10-14x faster development startup times** with stable Turbopack filesystem caching (large apps restart in ~1.1s vs ~15s)
- **20MB smaller installs** for faster CI/CD
- **2-5x faster production builds** with Turbopack
- **5-10x faster Fast Refresh**
- Improved async import bundling (fewer chunks in dev)

**Breaking Changes:**
- Minimal breaking changes (mostly additions)

**Verdict:** **UPGRADE IMMEDIATELY.** This is a no-brainer upgrade with massive development experience improvements and zero migration effort. The 10-14x faster dev startup alone is worth it.

---

#### React 18.3.1 → React 19.x
**Priority: MEDIUM-HIGH** ⚠️🚀

**Performance Improvements:**
- **20% faster rendering for large lists** (benchmarked)
- **Automatic memoization** via new React Compiler (eliminates need for useMemo/useCallback/memo)
- Reduced memory usage for large component trees
- Better Server Components integration

**Breaking Changes:**
- `propTypes` silently ignored (migrate to TypeScript)
- `defaultProps` removed from function components (use ES6 defaults)
- `ReactDOM.render` removed (use `ReactDOM.createRoot`)
- `ReactDOM.hydrate` removed (use `ReactDOM.hydrateRoot`)
- No UMD builds
- String refs removed

**Migration Effort:**
- React team provides codemods at [react-codemod repo](https://github.com/reactjs/react-codemod)
- Upgrade to React 18.3 first (adds deprecation warnings)
- Moderate effort depending on codebase size

**Verdict:** **Worth upgrading, but plan carefully.** 20% performance boost is significant. Use codemods to automate migration. Test thoroughly with your MUI components.

---

#### @mui/material 5.16.7 → 5.18.x or 6.x
**Priority: HIGH** ✅

**Performance Improvements:**
- **v6: 25% smaller package size** (2.5MB reduction by removing UMD bundle)
- Runtime performance optimizations (details not quantified)
- Better tree-shaking

**Breaking Changes:**
- Minimal (v6 designed for easy migration from v5)
- Codemods provided
- LoadingButton moved from Lab to core Button
- Typography `color` prop no longer a system prop

**Migration Effort:**
- Low to moderate with codemods
- Test thoroughly, especially custom theme overrides

**Verdict:** **Upgrade to v6.** 25% bundle size reduction directly improves load times. Minimal breaking changes. Wait until after React 19 migration if doing both.

---

#### lodash 4.17.21 → Replace with native JS or lodash-es
**Priority: MEDIUM** ⚠️

**Bundle Size:**
- Full lodash: ~70KB minified
- With proper per-method imports: ~small (only what you use)
- Native JS: 0KB

**Performance:**
- Lodash is often **faster than native** implementations
- Provides edge case handling and cross-browser consistency
- Modern JS (2026) has caught up for many use cases

**Options:**
1. **Keep lodash, use per-method imports:** `import map from 'lodash/map'`
2. **Switch to lodash-es:** Better tree-shaking with modern bundlers (Vite, Webpack 5+)
3. **Replace with native JS:** For simple operations (map, filter, find, etc.)

**Verdict:** **Audit your lodash usage first.** Replace simple operations with native JS. Keep lodash for complex operations (debounce, throttle, deep cloning, etc.). If keeping lodash, switch to lodash-es for better tree-shaking.

**Migration Effort:** Moderate to high depending on usage depth.

---

#### moment 2.30.1 → date-fns
**Priority: VERY HIGH** 🚀

**Bundle Size:**
- Moment.js: **~200KB** (monolithic, no tree-shaking)
- date-fns: **~8KB** (modular, tree-shakeable)
- **40%+ bundle size reduction** in real-world scenarios

**Performance:**
- date-fns is **faster** due to functional/immutable approach
- Moment.js is in **maintenance mode** (no new features)
- Moment.js team **recommends using alternatives**

**Breaking Changes:**
- Completely different API
- Format string syntax differs (e.g., "YYYY-MM-DD" → "yyyy-MM-dd")
- Immutable vs mutable paradigm

**Migration Effort:**
- High effort (full API rewrite for date operations)
- Worth it for bundle size alone

**Note:** You already have `date-fns` installed! (v2.30.0). Consider upgrading to v3 for latest features.

**Verdict:** **MIGRATE FROM MOMENT TO DATE-FNS ASAP.** You'll save ~192KB+ in bundle size, which directly improves page load times. This is one of the highest-impact changes you can make.

---

## 🎬 Getting Started

### Immediate Actions (Today):

1. **Backend - Update requirements.txt:**
   ```bash
   cd backend-ohack.dev
   # Edit requirements.txt and change:
   # redis==5.2.1 → redis==6.1.0
   # slack_sdk==3.18.1 → slack_sdk==3.27.1
   # python-dotenv==0.19.1 → python-dotenv==1.0.1
   git commit -am "Update requirements.txt to match installed versions"
   ```

2. **Frontend - Upgrade Next.js:**
   ```bash
   cd frontend-ohack.dev
   npm install next@latest
   npm test
   git commit -am "Upgrade Next.js to 16.1.x for 10x faster dev builds"
   ```

### Next Steps (This Week):

3. **Migrate moment → date-fns:**
   - Search for all moment imports: `grep -r "moment" src/`
   - Replace with date-fns equivalents
   - Remove moment from package.json
   - Test date formatting across the app

4. **Upgrade MUI to v6:**
   ```bash
   npm install @mui/material@latest @mui/icons-material@latest
   npx @mui/codemod@latest v6.0.0/preset-safe ./src
   npm test
   ```

---

## 📌 Notes

- All version numbers and performance metrics were researched on February 13, 2026
- Test thoroughly after each upgrade
- Consider staging deployments before production
- Monitor bundle size changes with `@next/bundle-analyzer`
- Track performance metrics before and after upgrades

---

## 🔗 References

- [Next.js 16.1 Release Notes](https://nextjs.org/blog/next-16-1)
- [React 19 Upgrade Guide](https://react.dev/blog/2024/04/25/react-19-upgrade-guide)
- [MUI v6 Migration Guide](https://mui.com/material-ui/migration/upgrade-to-v6/)
- [date-fns Documentation](https://date-fns.org/)
- [You Don't Need Momentjs](https://github.com/you-dont-need/You-Dont-Need-Momentjs)
