import express from "express";
const path = require("path");

class Handler extends Base {
  constructor(app) {
    super(app);
  }

  onRequest = (req, res) => res.send("ok");
}

const legacy = function (a, b) {
  return a + b;
};

function* ids() {
  yield 1;
}
