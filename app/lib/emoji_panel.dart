import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'emoji_data.dart';
import 'wechat_icons.dart';

/// 微信风格表情面板（分类 tab / 搜索 / 最近使用 / 退格），点击插入到 [controller]
class EmojiPanel extends StatefulWidget {
  final TextEditingController controller;
  final VoidCallback onChanged;
  const EmojiPanel({super.key, required this.controller, required this.onChanged});

  @override
  State<EmojiPanel> createState() => _EmojiPanelState();
}

class _EmojiPanelState extends State<EmojiPanel> {
  int _tab = 0;
  bool _search = false;
  final TextEditingController _query = TextEditingController();
  List<String> _recent = [];

  @override
  void initState() {
    super.initState();
    SharedPreferences.getInstance().then((p) {
      final r = p.getStringList('emoji_recent');
      if (r != null && mounted) setState(() => _recent = List<String>.from(r));
    });
  }

  @override
  void dispose() {
    _query.dispose();
    super.dispose();
  }

  void _insert(String e) {
    final c = widget.controller;
    final base = c.text;
    final sel = c.selection;
    // 在光标处插入（无有效光标时退化为追加），并把光标移到插入内容之后
    var start = sel.isValid ? sel.start : base.length;
    var end = sel.isValid ? sel.end : base.length;
    if (start > base.length) start = base.length;
    if (end > base.length) end = base.length;
    if (start > end) {
      final tmp = start;
      start = end;
      end = tmp;
    }
    final t = base.replaceRange(start, end, e);
    c.value = TextEditingValue(text: t, selection: TextSelection.collapsed(offset: start + e.length));
    _recent.remove(e);
    _recent.insert(0, e);
    if (_recent.length > 8) _recent = _recent.sublist(0, 8);
    SharedPreferences.getInstance().then((p) => p.setStringList('emoji_recent', _recent));
    widget.onChanged();
    setState(() {});
  }

  void _backspace() {
    if (_search) {
      final t = _query.text;
      if (t.isEmpty) return;
      final nt = t.characters.take(t.characters.length - 1).toString();
      _query.value = TextEditingValue(text: nt, selection: TextSelection.collapsed(offset: nt.length));
      setState(() {});
      return;
    }
    final t = widget.controller.text;
    if (t.isEmpty) return;
    final sel = widget.controller.selection;
    var start = sel.isValid ? sel.start : t.length;
    var end = sel.isValid ? sel.end : t.length;
    if (start > end) {
      final tmp = start;
      start = end;
      end = tmp;
    }
    if (start > t.length) start = t.length;
    if (end > t.length) end = t.length;
    String nt;
    int caret;
    if (start != end) {
      nt = t.replaceRange(start, end, ''); // 有选区：删除选中内容
      caret = start;
    } else {
      if (start <= 0) return;
      final head = t.substring(0, start).characters;
      final nHead = head.take(head.length - 1).toString(); // 删除光标前一个"字"
      nt = nHead + t.substring(start);
      caret = nHead.length;
    }
    widget.controller.value = TextEditingValue(text: nt, selection: TextSelection.collapsed(offset: caret));
    widget.onChanged();
    setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 290,
      color: const Color(0xFFF7F7F7),
      child: Column(children: [
        Container(
          height: 46,
          color: Colors.white,
          child: SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(horizontal: 8),
            child: Row(children: [
              _tabBtn(
                iconWidget: const WcSearchIcon(size: 22, color: Color(0xFF333333)),
                selected: _search,
                onTap: () => setState(() {
                  _search = true;
                  _query.clear();
                }),
              ),
              for (var i = 0; i < kEmojiCats.length; i++)
                _tabBtn(
                  text: kEmojiCats[i].icon,
                  selected: !_search && _tab == i,
                  onTap: () => setState(() {
                    _search = false;
                    _tab = i;
                  }),
                ),
            ]),
          ),
        ),
        Expanded(
          child: Stack(children: [
            Positioned.fill(child: _search ? _searchView() : _gridView()),
            Positioned(
              right: 10,
              bottom: 10,
              child: GestureDetector(
                onTap: _backspace,
                onLongPress: () {
                  widget.controller.clear();
                  widget.onChanged();
                  setState(() {});
                },
                child: Container(
                  width: 56,
                  height: 38,
                  decoration:
                      BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(8)),
                  child: const Icon(Icons.backspace_outlined, size: 20, color: Color(0xFF666666)),
                ),
              ),
            ),
          ]),
        ),
      ]),
    );
  }

  Widget _tabBtn(
      {Widget? iconWidget, String? text, required bool selected, required VoidCallback onTap}) {
    return GestureDetector(
      onTap: onTap,
      child: SizedBox(
        width: 40,
        height: 46,
        child: Center(
          child: Container(
            width: 36,
            height: 36,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              color: selected ? Colors.white : Colors.transparent,
              borderRadius: BorderRadius.circular(8),
              boxShadow: selected
                  ? const [BoxShadow(color: Color(0x14000000), blurRadius: 4, offset: Offset(0, 1))]
                  : null,
            ),
            child: iconWidget ?? Text(text ?? '', style: const TextStyle(fontSize: 21)),
          ),
        ),
      ),
    );
  }

  Widget _section(String title, List<String> items) {
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
        child: Text(title, style: const TextStyle(fontSize: 13, color: Color(0xFF888888))),
      ),
      GridView.count(
        crossAxisCount: 8,
        shrinkWrap: true,
        physics: const NeverScrollableScrollPhysics(),
        childAspectRatio: 1.15,
        padding: const EdgeInsets.symmetric(horizontal: 10),
        children: [
          for (final it in items)
            GestureDetector(
              onTap: () => _insert(emojiOf(it)),
              child: Center(child: Text(emojiOf(it), style: const TextStyle(fontSize: 26))),
            ),
        ],
      ),
    ]);
  }

  Widget _gridView() {
    final items = kEmojiCats[_tab].items;
    return ListView(
      padding: const EdgeInsets.only(bottom: 56),
      children: [
        if (_recent.isNotEmpty) _section('最近使用', _recent),
        _section('所有表情', items),
      ],
    );
  }

  Widget _searchView() {
    final q = _query.text.trim();
    final results = <String>[];
    if (q.isNotEmpty) {
      for (final cat in kEmojiCats) {
        for (final it in cat.items) {
          if (it.contains(q)) results.add(it);
        }
      }
    }
    return Column(children: [
      Container(
        color: Colors.white,
        padding: const EdgeInsets.fromLTRB(12, 8, 12, 10),
        child: Container(
          height: 34,
          padding: const EdgeInsets.symmetric(horizontal: 10),
          decoration:
              BoxDecoration(color: const Color(0xFFF2F2F2), borderRadius: BorderRadius.circular(6)),
          child: Row(children: [
            const WcSearchIcon(size: 18, color: Color(0xFF999999)),
            const SizedBox(width: 6),
            Expanded(
              child: TextField(
                controller: _query,
                autofocus: true,
                style: const TextStyle(fontSize: 15),
                decoration: const InputDecoration(
                  isDense: true,
                  border: InputBorder.none,
                  contentPadding: EdgeInsets.zero,
                  hintText: '搜索表情',
                ),
                onChanged: (_) => setState(() {}),
              ),
            ),
          ]),
        ),
      ),
      Expanded(
        child: q.isEmpty
            ? const Center(
                child: Text('输入关键词搜索表情',
                    style: TextStyle(fontSize: 13, color: Color(0xFFAAAAAA))))
            : GridView.count(
                crossAxisCount: 8,
                childAspectRatio: 1.15,
                padding: const EdgeInsets.fromLTRB(10, 10, 10, 56),
                children: [
                  for (final it in results)
                    GestureDetector(
                      onTap: () => _insert(emojiOf(it)),
                      child: Center(child: Text(emojiOf(it), style: const TextStyle(fontSize: 26))),
                    ),
                ],
              ),
      ),
    ]);
  }
}
