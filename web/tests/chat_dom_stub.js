/* chat_dom_stub.js · 极简 DOM 桩（仅测试用，零依赖）
 * 只实现 ChatPager 需要的少数 API：createElement / insertBefore /
 * removeChild / replaceChild / children / parentElement / className /
 * textContent / onclick，以及可写的 scrollTop / scrollHeight / clientHeight。 */

class FakeElement {
  constructor(tag, doc) {
    this.tagName = tag;
    this.ownerDocument = doc;
    this.children = [];
    this.parentElement = null;
    this.className = "";
    this.textContent = "";
    this.onclick = null;
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.clientHeight = 0;
  }

  appendChild(node) {
    return this.insertBefore(node, null);
  }

  insertBefore(node, ref) {
    if (node.parentElement) node.parentElement.removeChild(node);
    const idx = ref ? this.children.indexOf(ref) : -1;
    if (idx < 0) this.children.push(node);
    else this.children.splice(idx, 0, node);
    node.parentElement = this;
    return node;
  }

  removeChild(node) {
    const idx = this.children.indexOf(node);
    if (idx < 0) throw new Error("removeChild: 不是子节点");
    this.children.splice(idx, 1);
    node.parentElement = null;
    return node;
  }

  replaceChild(next, old) {
    const idx = this.children.indexOf(old);
    if (idx < 0) throw new Error("replaceChild: 不是子节点");
    old.parentElement = null;
    next.parentElement = this;
    this.children[idx] = next;
    return old;
  }

  /** 测试辅助：取 className 包含指定片段的子孙 */
  findAll(cls) {
    const out = [];
    const walk = (n) => {
      for (const c of n.children) {
        if (c.className.includes(cls)) out.push(c);
        walk(c);
      }
    };
    walk(this);
    return out;
  }
}

class FakeDocument {
  createElement(tag) { return new FakeElement(tag, this); }
}

/** 递归统计子树元素节点数（含 host 自身） */
function countNodes(node) {
  let n = 1;
  for (const c of node.children) n += countNodes(c);
  return n;
}

module.exports = { FakeElement, FakeDocument, countNodes };
