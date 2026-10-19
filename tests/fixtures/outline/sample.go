package store

import (
	"fmt"
	alias "example.com/x/y"
)

type Repo struct {
	items []int
}

type Saver interface {
	Save(x int) error
}

func (r *Repo) Save(x int) error {
	r.items = append(r.items, x)
	return nil
}

func New() *Repo {
	return &Repo{}
}
