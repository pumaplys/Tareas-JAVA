package tema2.t03;

public class ej10 {
    public static void main(String[] args) {
        int a = 4;
        int b = 1;
        int c = 8;
        int d = 12;

        int sumados = sumar(a, b);
        int sumatres = sumar(d, b, c);
        int sumacuatro = sumar(a, b, c, d);

        System.out.println("La suma de dos: " + sumados);
        System.out.println("La suma de tres: " + sumatres);
        System.out.println("La suma de cuatro: " + sumacuatro);
    }

    static int sumar(int a, int b) {
        return a + b;
    }

    static int sumar(int d, int b, int c) {
        return d + b + c;
    }

    static int sumar(int a, int b, int c, int d) {
        return a + b + c + d;
    }
}
