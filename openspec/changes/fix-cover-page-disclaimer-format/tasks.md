## 1. Fix "正本" Mark Rendering

- [ ] 1.1 Add font color black (RGBColor(0,0,0)) to "正本" run in _build_docx_bytes
- [ ] 1.2 Verify "正本" table absolute positioning (tblpX/tblpY) values are correct for top-right placement

## 2. Remove Extra Blank Page After Disclaimer

- [ ] 2.1 Remove `document.add_page_break()` at line 3314 after `_add_disclaimer_page(document)` call

## 3. Fix Cover Block Font Rendering

- [ ] 3.1 In first cover rendering section (lines 3715-3747), remove default fallback for font_size=16.0 when font metadata has no size; use font metadata values directly
- [ ] 3.2 Apply same font rendering fix to cover section title block rendering

## 4. Fix Bid Date Placeholder Filling

- [ ] 4.1 Extract bid_open_time from analysis_data metadata (key_dates.bid_opening / bid_open_time) in _build_docx_bytes
- [ ] 4.2 Replace `cover_bid_time` assignment (line 3258) to use extracted bid_open_time instead of utc_now()
- [ ] 4.3 Add "投标日期" placeholder replacement to `_fill_placeholder_text` function

## 5. Verify and Test

- [ ] 5.1 Verify all changes work together without regression by reviewing the modified code paths
- [ ] 5.2 Check that cover page generates with correct font/size/color for all four issues
